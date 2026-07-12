"""
graph_pattern_detector.py
=========================
Stage 2 of the fraud-analytics-job pipeline.

Detects seven structural and semantic behavioural patterns in the Nominations
graph for each tenant and upserts findings into dbo.GraphPatternFindings.

Pattern catalogue
-----------------
1. Ring              — directed cycles ≥ 3 hops (networkx simple_cycles)
2. SuperNominator    — degree-distribution outlier (mean + 2σ, min 3× median)
3. Desert            — whole team absent from both sides of the graph
4. ApproverAffinity  — per-pair approval rate ≥ 2× tenant baseline, min 5 noms
5. CopyPaste         — cosine similarity ≥ 0.92 between descriptions, min cluster 3
6. TransactionalLanguage — personal-benefit regex phrases in description text
7. HiddenCandidate   — name appears ≥ 5× in descriptions but never a BeneficiaryId

Environment variables (all injected by the Container Apps Job)
--------------------------------------------------------------
  SQL_SERVER            Azure SQL FQDN
  SQL_DATABASE          Database name
  SQL_USER              SQL login
  SQL_PASSWORD          SQL password
  GRAPH_FINDINGS_TABLE  Target table (default: dbo.GraphPatternFindings)
  LOGGING_LEVEL         Python log level (default: INFO)
"""

from __future__ import annotations

import gc
import hashlib
import json
import logging
import os
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import networkx as nx
import numpy as np
import pyodbc
from pathlib import Path
from dotenv import load_dotenv

# Same .env loading as train_fraud_model.py / forecast_models.py so this stage
# can be run standalone locally. No-op in Container Apps (env injected).
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

logger = logging.getLogger(__name__)

# ── Database helpers ──────────────────────────────────────────────────────────

def _load_tenant_integrity_config(conn: pyodbc.Connection, tenant_id: int) -> dict:
    """
    Load and parse integrity_config JSON from dbo.Tenants for this tenant.

    Returns the parsed dict, or {} if the column is NULL, missing, or invalid.
    Callers access keys via .get() with explicit defaults so partial configs
    are safe (e.g. a tenant that only sets score_routing still gets the
    correct detection window from the env-var default).
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT integrity_config FROM dbo.Tenants WHERE TenantId = ?", tenant_id
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "Invalid JSON in integrity_config for tenant %d — using defaults",
            tenant_id,
        )
        return {}


from db_conn import connect


def _get_connection() -> pyodbc.Connection:
    """Connect to Azure SQL via Managed Identity (see db_conn.connect)."""
    return connect()


# ── Graph sync ────────────────────────────────────────────────────────────────

def sync_graph_tables(conn: pyodbc.Connection) -> None:
    """
    Refresh NomGraph_Person and NomGraph_Nominated from the live tables.
    Full DELETE + INSERT on every run — tables are small enough that
    truncate-and-reload is simpler than a merge.
    """
    cur = conn.cursor()

    logger.info("Syncing NomGraph_Person …")
    cur.execute("DELETE FROM dbo.NomGraph_Person")
    cur.execute("""
        INSERT INTO dbo.NomGraph_Person (UserId, FullName, TenantId)
        SELECT UserId,
               ISNULL(FirstName + ' ' + LastName, CAST(UserId AS NVARCHAR)),
               TenantId
        FROM   dbo.Users
    """)

    logger.info("Syncing NomGraph_Nominated …")
    cur.execute("DELETE FROM dbo.NomGraph_Nominated")
    cur.execute("""
        INSERT INTO dbo.NomGraph_Nominated
              ($from_id, $to_id, NominationId, Amount, Status, NomDate)
        SELECT
            (SELECT $node_id FROM dbo.NomGraph_Person WHERE UserId = n.NominatorId),
            (SELECT $node_id FROM dbo.NomGraph_Person WHERE UserId = n.BeneficiaryId),
            n.NominationId,
            n.Amount,
            n.Status,
            CAST(n.NominationDate AS DATE)
        FROM   dbo.Nominations n
        WHERE  EXISTS (SELECT 1 FROM dbo.NomGraph_Person WHERE UserId = n.NominatorId)
          AND  EXISTS (SELECT 1 FROM dbo.NomGraph_Person WHERE UserId = n.BeneficiaryId)
    """)

    conn.commit()
    logger.info("Graph tables synced.")


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_nominations(
    conn: pyodbc.Connection,
    tenant_id: int,
    window_days: int,
) -> list[dict]:
    """
    Return Approved/Paid nominations for a tenant within the rolling detection window.

    Only Status IN ('Approved', 'Paid') are loaded — these represent real
    financial exposure.  Pending/Rejected nominations are excluded so rings,
    super-nominators, and copy-paste clusters reflect committed spend.

    window_days controls how far back to look.  All seven detectors share
    this window; the ring / approver-affinity detectors need the longest
    horizon (~180 days), so that value drives the single shared parameter.

    Set DETECTION_WINDOW_DAYS=3650 on first deploy to process full history.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT n.NominationId, n.NominatorId, n.BeneficiaryId,
               n.ApproverId,   n.Status,      n.Amount,
               n.NominationDescription AS Description,  n.NominationDate AS CreatedAt
        FROM   dbo.Nominations n
        JOIN   dbo.Users u ON u.UserId = n.NominatorId
        WHERE  u.TenantId = ?
          AND  n.NominationDate >= DATEADD(DAY, -?, GETDATE())
          AND  n.Status IN ('Approved', 'Paid')
    """, tenant_id, window_days)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _load_users(conn: pyodbc.Connection, tenant_id: int) -> list[dict]:
    """Return all users for a tenant (no date filter — used for desert detection)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT u.UserId,
               ISNULL(u.FirstName + ' ' + u.LastName, CAST(u.UserId AS NVARCHAR)) AS FullName,
               u.ManagerId
        FROM   dbo.Users u
        WHERE  u.TenantId = ?
    """, tenant_id)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _load_ever_active_user_ids(conn: pyodbc.Connection, tenant_id: int) -> set[int]:
    """
    Return the set of UserIds that have ever appeared on either side of a
    nomination — with no date filter.

    Used exclusively by detect_deserts: a user who nominated someone 8 months
    ago should not be flagged as desert just because the rolling window
    excludes that nomination.  We only want to flag users who have been
    completely absent from nominations since joining.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT n.NominatorId AS UserId
        FROM   dbo.Nominations n
        JOIN   dbo.Users u ON u.UserId = n.NominatorId
        WHERE  u.TenantId = ?
        UNION
        SELECT DISTINCT n.BeneficiaryId
        FROM   dbo.Nominations n
        JOIN   dbo.Users u ON u.UserId = n.NominatorId
        WHERE  u.TenantId = ?
    """, tenant_id, tenant_id)
    return {row[0] for row in cur.fetchall()}


def _load_tenants(conn: pyodbc.Connection) -> list[int]:
    cur = conn.cursor()
    cur.execute("SELECT TenantId FROM dbo.Tenants ORDER BY TenantId")
    return [row[0] for row in cur.fetchall()]


# ── Finding helpers ───────────────────────────────────────────────────────────

def _fingerprint(
    tenant_id: int,
    pattern_type: str,
    affected_users: list[int],   # must already be sorted
    nomination_ids: list[int],   # must already be sorted
) -> str:
    """
    Deterministic SHA-256 fingerprint (64 hex chars) of a finding's content.

    Inputs must be pre-sorted so the hash is stable regardless of detection
    order.  The fingerprint is stored in FindingHash and used to prevent
    duplicate inserts across runs.

    Same content → same hash → not re-inserted (idempotent).
    Evolved content (e.g. new nominations added to a ring) → new hash → inserted.
    """
    key = f"{tenant_id}|{pattern_type}|{json.dumps(affected_users)}|{json.dumps(nomination_ids)}"
    return hashlib.sha256(key.encode()).hexdigest()


def _finding(
    tenant_id: int,
    run_id: str,
    pattern_type: str,
    severity: str,
    affected_users: list[int],   # must already be sorted
    nomination_ids: list[int],   # must already be sorted
    detail: str,
    total_amount: int = 0,
) -> dict[str, Any]:
    return {
        "TenantId":      tenant_id,
        "PatternType":   pattern_type,
        "Severity":      severity,
        "AffectedUsers": json.dumps(affected_users),
        "NominationIds": json.dumps(nomination_ids),
        "Detail":        detail[:1000],
        "DetectedAt":    datetime.now(timezone.utc),
        "RunId":         run_id,
        "FindingHash":   _fingerprint(tenant_id, pattern_type, affected_users, nomination_ids),
        "TotalAmount":   total_amount,
    }


def _load_existing_hashes(
    conn: pyodbc.Connection,
    tenant_id: int,
    table: str,
) -> set[str]:
    """
    Return the set of FindingHash values already stored for this tenant.
    Used to filter out duplicate findings before inserting.
    NULL hashes (rows from before migration 0008) are excluded — they will
    be naturally re-evaluated by the detector as the window rolls forward.
    """
    cur = conn.cursor()
    cur.execute(f"""
        SELECT FindingHash
        FROM   {table}
        WHERE  TenantId    = ?
          AND  FindingHash IS NOT NULL
    """, tenant_id)
    return {row[0] for row in cur.fetchall()}


def _save_findings(
    conn: pyodbc.Connection,
    findings: list[dict],
    table: str,
    existing_hashes: set[str],
) -> None:
    """
    Insert findings whose FindingHash is not already in the table.
    Skipped findings are logged so the operator can see the dedup effect.
    The DB-level unique index on (TenantId, FindingHash) is a safety net
    in case of race conditions or logic bugs.
    """
    if not findings:
        return

    # Two-pass dedup:
    # 1. Filter against hashes already in the DB (loaded before detection ran)
    # 2. Filter internal duplicates within this run's findings — two detectors
    #    could theoretically produce identical content, and existing_hashes
    #    wouldn't catch them since neither is in the DB yet.
    seen_this_run: set[str] = set()
    new_findings:  list[dict] = []

    for f in findings:
        h = f["FindingHash"]
        if h in existing_hashes:
            continue          # already in DB from a previous run
        if h in seen_this_run:
            continue          # duplicate within this run
        seen_this_run.add(h)
        new_findings.append(f)

    skipped = len(findings) - len(new_findings)
    logger.info(
        "  Dedup: %d candidate(s), %d new, %d skipped.",
        len(findings), len(new_findings), skipped,
    )
    if not new_findings:
        logger.info("  No new findings to save.")
        return

    cur = conn.cursor()
    sql = f"""
        INSERT INTO {table}
               (TenantId, PatternType, Severity,
                AffectedUsers, NominationIds, Detail, DetectedAt, RunId, FindingHash,
                TotalAmount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    rows = [
        (
            f["TenantId"],
            f["PatternType"],
            f["Severity"],
            f["AffectedUsers"],
            f["NominationIds"],
            f["Detail"],
            f["DetectedAt"],
            f["RunId"],
            f["FindingHash"],
            f.get("TotalAmount", 0),
        )
        for f in new_findings
    ]
    cur.executemany(sql, rows)
    conn.commit()
    logger.info("  Saved %d new finding(s) to %s.", len(new_findings), table)


# ── Pattern 1: Rings ──────────────────────────────────────────────────────────

def detect_rings(
    nominations: list[dict],
    users: list[dict],
    tenant_id: int,
    run_id: str,
    max_cluster_size: int = 0,
) -> list[dict]:
    """
    Detects nomination rings using simple_cycles() with frozenset deduplication.

    Algorithm
    ---------
    For each ring size from max_cluster_size down to 3:
      1. Use simple_cycles(G, length_bound=size) to find all cycles up to
         that length, filtered to exactly `size` nodes.
      2. For each cycle, compute frozenset(cycle) as the dedup key.
         This collapses all permutations of the same user group:
           [A,B,C], [B,C,A], [C,A,B], [A,C,B] → frozenset({A,B,C})
         Each unique user group is reported exactly once regardless of
         how many directed paths exist through it.
      3. Skip any frozenset already seen in a larger-size pass — prevents
         the same users appearing in both a 4-node and a 3-node finding.

    Why not SCC?
      strongly_connected_components() on a dense graph (291 users, 11 K
      nominations) produces one giant 282-node cluster that is analytically
      useless.  simple_cycles() with length_bound + frozenset dedup finds
      the genuine tight rings the seeder planted.

    max_cluster_size: largest ring size to report (default 0 = unlimited,
      capped internally at 8 to prevent DFS explosion on dense graphs).
      Set via RING_MAX_CLUSTER_SIZE env var.

    Severity — financial exposure (all nominations are already Approved/Paid):
      TotalAmount ≥ 10 000 → Critical
      TotalAmount ≥  5 000 → High
      TotalAmount ≥  1 000 → Medium
      TotalAmount  <  1 000 → Low
    """
    # Hard cap: simple_cycles DFS is exponential beyond 8 hops regardless
    # of what the operator configures.
    HARD_CAP = 8
    upper = min(max_cluster_size, HARD_CAP) if max_cluster_size > 0 else HARD_CAP

    G = nx.DiGraph()
    # Map edge → list of (NominationId, Amount) for TotalAmount computation
    edge_nominations: dict[tuple, list[tuple[int, int]]] = defaultdict(list)

    for nom in nominations:
        src, dst = nom["NominatorId"], nom["BeneficiaryId"]
        G.add_edge(src, dst)
        edge_nominations[(src, dst)].append(
            (nom["NominationId"], nom["Amount"] or 0)
        )

    # Build user ID → display name lookup so ring descriptions are human-readable
    user_name: dict[int, str] = {u["UserId"]: u["FullName"] for u in users}

    findings:       list[dict]        = []
    seen_user_sets: set[frozenset]    = set()

    # Iterate largest → smallest so that if {A,B,C,D} is found first,
    # the subset {A,B,C} is still reported — they are distinct rings.
    # Users already in a seen frozenset are NOT suppressed for smaller
    # rings; only the identical frozenset is deduplicated.
    for size in range(upper, 2, -1):   # e.g. 4, 3
        for cycle in nx.simple_cycles(G, length_bound=size):
            if len(cycle) != size:
                continue  # length_bound yields cycles UP TO size; skip shorter

            key = frozenset(cycle)
            if key in seen_user_sets:
                continue  # same user group already reported at this or larger size
            seen_user_sets.add(key)

            members = sorted(key)

            # Collect nomination IDs and amounts on edges that form this cycle
            nom_ids:      list[int] = []
            total_amount: int       = 0
            for i in range(size):
                src = cycle[i]
                dst = cycle[(i + 1) % size]
                for nom_id, amount in edge_nominations.get((src, dst), []):
                    nom_ids.append(nom_id)
                    total_amount += amount

            # Severity based on financial exposure
            # (all loaded nominations are Approved/Paid — amount is committed spend)
            if total_amount >= 10_000:
                severity = "Critical"
            elif total_amount >= 5_000:
                severity = "High"
            elif total_amount >= 1_000:
                severity = "Medium"
            else:
                severity = "Low"

            member_names = [user_name.get(u, str(u)) for u in cycle]
            findings.append(_finding(
                tenant_id, run_id, "Ring", severity,
                members, sorted(set(nom_ids)),
                f"{size}-person directed nomination ring detected. "
                f"Members: {' → '.join(member_names)} → {member_names[0]}. "
                f"Each member nominates the next in a closed cycle, consistent with "
                f"coordinated reciprocal recognition. "
                f"(Total approved/paid: ${total_amount:,})",
                total_amount=total_amount,
            ))

    logger.info(
        "  Rings: %d detected (sizes 3–%d, frozenset dedup).",
        len(findings), upper,
    )
    return findings


# ── Pattern 2: Super-nominators ───────────────────────────────────────────────

def detect_super_nominators(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
) -> list[dict]:
    """
    Users whose out-degree (nominations sent) is a statistical outlier.
    Threshold: mean + 2σ AND at least 3× the median.
    Minimum absolute count: 5 nominations sent.
    """
    out_degree:  dict[int, list[int]] = defaultdict(list)
    out_amounts: dict[int, int]       = defaultdict(int)
    for nom in nominations:
        out_degree[nom["NominatorId"]].append(nom["NominationId"])
        out_amounts[nom["NominatorId"]] += nom["Amount"] or 0

    if len(out_degree) < 3:
        return []

    counts = np.array([len(v) for v in out_degree.values()], dtype=float)
    mean   = counts.mean()
    std    = counts.std()
    median = float(np.median(counts))

    threshold = max(mean + 2 * std, 3 * median, 5.0)

    findings: list[dict] = []
    for user_id, nom_ids in out_degree.items():
        cnt = len(nom_ids)
        if cnt >= threshold:
            total_amount = out_amounts[user_id]
            severity = "High" if cnt >= threshold * 1.5 else "Medium"
            findings.append(_finding(
                tenant_id, run_id, "SuperNominator", severity,
                [user_id], nom_ids,
                f"User {user_id} sent {cnt} nominations "
                f"(tenant mean={mean:.1f}, threshold={threshold:.1f}, "
                f"total approved/paid: ${total_amount:,})",
                total_amount=total_amount,
            ))

    logger.info("  SuperNominators: %d detected", len(findings))
    return findings


# ── Pattern 3: Nomination deserts ─────────────────────────────────────────────

def detect_deserts(
    ever_active_ids: set[int],
    users: list[dict],
    tenant_id: int,
    run_id: str,
) -> list[dict]:
    """
    Teams (grouped by ManagerId) where no member has ever appeared on either
    side of any nomination — neither nominator nor beneficiary.
    Minimum team size: 3 members (singletons and pairs excluded).

    Uses ever_active_ids (all-time, no date filter) rather than the rolling
    window nominations list.  A user who nominated someone 8 months ago should
    not be flagged as a desert just because that nomination falls outside the
    current detection window.
    """
    all_participants = ever_active_ids

    # Group by manager
    teams: dict[Any, list[int]] = defaultdict(list)
    for user in users:
        if user["ManagerId"] is not None:
            teams[user["ManagerId"]].append(user["UserId"])

    findings: list[dict] = []
    for manager_id, members in teams.items():
        if len(members) < 3:
            continue
        absent = [m for m in members if m not in all_participants]
        if len(absent) == len(members):  # entire team is absent
            findings.append(_finding(
                tenant_id, run_id, "Desert", "Medium",
                members, [],
                f"Team under manager {manager_id} ({len(members)} members) "
                "has zero nomination activity on either side.",
            ))

    logger.info("  Deserts: %d detected", len(findings))
    return findings


# ── Pattern 4: Approver affinity ──────────────────────────────────────────────

def detect_approver_affinity(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
) -> list[dict]:
    """
    Specific (nominator, approver) pairs whose approval rate is ≥ 2× the
    tenant-wide baseline, with at least 5 nominations in the pair sample.

    "Approval" means Status in ('Approved', 'Paid').
    """
    approved_statuses = {"Approved", "Paid"}

    total   = len(nominations)
    n_approved = sum(1 for n in nominations if n["Status"] in approved_statuses)
    if total == 0:
        return []
    baseline = n_approved / total

    pair_total:    dict[tuple, int]       = defaultdict(int)
    pair_approved: dict[tuple, int]       = defaultdict(int)
    pair_noms:     dict[tuple, list[int]] = defaultdict(list)
    pair_amounts:  dict[tuple, int]       = defaultdict(int)

    for nom in nominations:
        if nom["ApproverId"] is None:
            continue
        key = (nom["NominatorId"], nom["ApproverId"])
        pair_total[key]   += 1
        pair_noms[key].append(nom["NominationId"])
        pair_amounts[key] += nom["Amount"] or 0
        if nom["Status"] in approved_statuses:
            pair_approved[key] += 1

    findings: list[dict] = []
    for key, cnt in pair_total.items():
        if cnt < 5:
            continue
        rate = pair_approved[key] / cnt
        if rate >= 2 * baseline and baseline > 0:
            nominator_id, approver_id = key
            total_amount = pair_amounts[key]
            severity = "High" if rate >= 3 * baseline else "Medium"
            findings.append(_finding(
                tenant_id, run_id, "ApproverAffinity", severity,
                [nominator_id, approver_id], pair_noms[key],
                f"Nominator {nominator_id} / Approver {approver_id}: "
                f"approval rate {rate:.0%} vs tenant baseline {baseline:.0%} "
                f"({cnt} nominations, total approved/paid: ${total_amount:,})",
                total_amount=total_amount,
            ))

    logger.info("  ApproverAffinity: %d detected", len(findings))
    return findings


# ── Embedding cache helpers ───────────────────────────────────────────────────

def _evict_stale_embeddings(conn: pyodbc.Connection, window_days: int) -> None:
    """
    Delete cached embeddings for nominations no longer within the active
    detection window (or no longer Approved/Paid).

    Called once per job run — before per-tenant processing — to keep the
    NomGraph_NominationEmbedding table bounded to roughly
    DETECTION_WINDOW_DAYS × approval_rate rows.
    """
    cur = conn.cursor()
    cur.execute("""
        DELETE e
        FROM   dbo.NomGraph_NominationEmbedding e
        WHERE  NOT EXISTS (
            SELECT 1
            FROM   dbo.Nominations n
            WHERE  n.NominationId   = e.NominationId
              AND  n.NominationDate >= DATEADD(DAY, -?, GETDATE())
              AND  n.Status         IN ('Approved', 'Paid')
        )
    """, window_days)
    deleted = cur.rowcount
    conn.commit()
    if deleted:
        logger.info("Evicted %d stale embedding(s) from cache.", deleted)


def _load_cached_embeddings(
    conn: pyodbc.Connection,
    nom_ids: list[int],
) -> dict[int, np.ndarray]:
    """
    Return {NominationId: embedding_vector} for all IDs that already have a
    cached row in NomGraph_NominationEmbedding.

    Chunked into batches of 2 000 to stay within SQL Server's 2 100-parameter
    limit per statement.
    """
    if not nom_ids:
        return {}

    result: dict[int, np.ndarray] = {}
    cur = conn.cursor()
    CHUNK = 2_000

    for i in range(0, len(nom_ids), CHUNK):
        batch = nom_ids[i : i + CHUNK]
        placeholders = ",".join("?" * len(batch))
        cur.execute(
            f"SELECT NominationId, Embedding "
            f"FROM   dbo.NomGraph_NominationEmbedding "
            f"WHERE  NominationId IN ({placeholders})",
            batch,
        )
        for row in cur.fetchall():
            # pyodbc returns VARBINARY as memoryview; bytes() converts it
            result[row[0]] = np.frombuffer(bytes(row[1]), dtype=np.float32).copy()

    return result


def _save_embeddings(
    conn: pyodbc.Connection,
    embeddings: dict[int, np.ndarray],
) -> None:
    """
    Persist newly computed embedding vectors to the cache table.

    Uses INSERT … WHERE NOT EXISTS so a re-run that encounters a race
    condition (two job instances starting simultaneously) is safe.
    Each vector is stored as raw float32 bytes via tobytes().
    """
    if not embeddings:
        return

    cur = conn.cursor()
    rows = [
        (nom_id, vec.astype(np.float32).tobytes(), nom_id)
        for nom_id, vec in embeddings.items()
    ]
    cur.executemany("""
        INSERT INTO dbo.NomGraph_NominationEmbedding (NominationId, Embedding, EmbeddedAt)
        SELECT ?, CAST(? AS VARBINARY(MAX)), GETUTCDATE()
        WHERE  NOT EXISTS (
            SELECT 1 FROM dbo.NomGraph_NominationEmbedding WHERE NominationId = ?
        )
    """, rows)
    conn.commit()
    logger.info("  Cached %d new embedding(s).", len(embeddings))


# ── Pattern 5: Copy-paste fraud ───────────────────────────────────────────────

def detect_copy_paste(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
    conn: pyodbc.Connection,
    similarity_threshold: float = 0.92,
    min_cluster_size: int = 3,
    chunk_size: int = 512,
) -> list[dict]:
    """
    Clusters of nominations whose description embeddings are mutually similar
    (cosine ≥ similarity_threshold). Uses sentence-transformers for embeddings
    and union-find for cluster formation.

    Embedding cache
    ---------------
    Approved/Paid nomination text is immutable, so embeddings computed on a
    previous run are valid forever.  On each run the detector:

      1. Queries NomGraph_NominationEmbedding for all eligible NominationIds.
      2. Encodes only the *delta* — nominations with no cached row.
         At steady state this is typically one week's worth of new approvals,
         reducing encoding work by ~96 % compared to a cold start.
      3. Persists the new vectors to the cache for future runs.
      4. Assembles the full embedding matrix from cached + new vectors.

    Memory strategy
    ---------------
    Instead of materialising the full N×N similarity matrix (which is ~500 MB
    at 11 K nominations), we process row-chunks of `chunk_size` at a time.
    Each chunk produces a (chunk_size × N) slice that is discarded after the
    pairs above the threshold are recorded.
    Peak extra memory per chunk: chunk_size × N × 4 bytes
    = 512 × 11 196 × 4 ≈ 23 MB — manageable inside 4 Gi.

    The sentence-transformers model (~500 MB resident) is only loaded when
    there are uncached nominations to encode, and is explicitly deleted and
    garbage-collected immediately after encoding.

    Only clusters of ≥ min_cluster_size nominations are flagged.
    """
    # Filter to nominations with non-trivial descriptions
    eligible = [
        n for n in nominations
        if n.get("Description") and len(n["Description"].strip()) > 20
    ]
    if len(eligible) < min_cluster_size:
        return []

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError:
        logger.warning("sentence-transformers not available — skipping CopyPaste")
        return []

    # ── Step 1: load cached embeddings ───────────────────────────────────────
    nom_ids = [n["NominationId"] for n in eligible]
    cached  = _load_cached_embeddings(conn, nom_ids)
    n_cached = len(cached)
    n_total  = len(eligible)
    logger.info(
        "  Embedding cache: %d/%d hits (%.0f%% cached).",
        n_cached, n_total,
        100 * n_cached / n_total if n_total else 0,
    )

    # ── Step 2: encode only the delta ────────────────────────────────────────
    to_embed = [n for n in eligible if n["NominationId"] not in cached]
    new_embeddings: dict[int, np.ndarray] = {}

    if to_embed:
        texts = [n["Description"] for n in to_embed]
        logger.info("  Encoding %d new description(s) …", len(texts))

        model = SentenceTransformer("all-MiniLM-L6-v2")
        vecs  = model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=64,
            show_progress_bar=False,
        )
        vecs = np.array(vecs, dtype=np.float32)

        # Free the ~500 MB PyTorch model immediately after encoding
        del model
        gc.collect()

        for i, n in enumerate(to_embed):
            new_embeddings[n["NominationId"]] = vecs[i]

        # ── Step 3: persist new vectors to cache ─────────────────────────────
        _save_embeddings(conn, new_embeddings)

    # ── Step 4: assemble full embedding matrix in eligible order ─────────────
    # NOTE: do NOT use cached.get(key, new_embeddings[key]) here.
    # dict.get() eagerly evaluates its default argument, so new_embeddings[key]
    # is always executed — raising KeyError when new_embeddings is empty
    # (i.e. 100% cache hit).  Use a conditional expression instead.
    all_vecs = np.stack([
        cached[n["NominationId"]] if n["NominationId"] in cached
        else new_embeddings[n["NominationId"]]
        for n in eligible
    ])  # shape: (len(eligible), 384)

    # Rename to match the rest of the function
    embeddings = all_vecs
    del all_vecs, cached, new_embeddings
    gc.collect()

    # ── Chunked union-find ────────────────────────────────────────────────────
    # For each row-chunk, compute a (chunk × N) similarity slice and union
    # pairs that exceed the threshold.  We never hold the full N×N matrix.
    n = len(eligible)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    # Track per-pair similarity for avg_sim calculation later (only above-threshold pairs)
    pair_sims: dict[tuple[int, int], float] = {}

    for start in range(0, n, chunk_size):
        end = min(start + chunk_size, n)
        chunk = embeddings[start:end]            # (chunk_size × 384)
        sims  = chunk @ embeddings.T             # (chunk_size × N)

        for local_i, global_i in enumerate(range(start, end)):
            # Only check j > global_i to avoid double-processing
            row = sims[local_i, global_i + 1:]
            hits = np.where(row >= similarity_threshold)[0]
            for offset in hits:
                global_j = global_i + 1 + int(offset)
                union(global_i, global_j)
                pair_sims[(global_i, global_j)] = float(sims[local_i, global_j])

        del sims  # release chunk slice immediately

    del embeddings
    gc.collect()

    # ── Collect clusters ──────────────────────────────────────────────────────
    clusters: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        clusters[find(idx)].append(idx)

    findings: list[dict] = []
    for _root, members in clusters.items():
        if len(members) < min_cluster_size:
            continue
        nom_ids      = [eligible[i]["NominationId"] for i in members]
        user_ids     = list({eligible[i]["NominatorId"] for i in members})
        total_amount = sum(eligible[i]["Amount"] or 0 for i in members)

        # avg_sim from recorded above-threshold pairs within this cluster
        cluster_set = set(members)
        cluster_pairs = [
            v for (a, b), v in pair_sims.items()
            if a in cluster_set and b in cluster_set
        ]
        avg_sim  = float(np.mean(cluster_pairs)) if cluster_pairs else similarity_threshold
        severity = "High" if avg_sim >= 0.97 else "Medium"

        findings.append(_finding(
            tenant_id, run_id, "CopyPaste", severity,
            sorted(user_ids), sorted(nom_ids),
            f"Cluster of {len(members)} nominations with avg cosine "
            f"similarity {avg_sim:.3f} (threshold {similarity_threshold}, "
            f"total approved/paid: ${total_amount:,})",
            total_amount=total_amount,
        ))

    logger.info("  CopyPaste: %d clusters detected", len(findings))
    return findings


# ── Pattern 6: Transactional language ────────────────────────────────────────

_TRANSACTIONAL_PATTERNS = re.compile(
    r"\b("
    r"helped me|help me|"
    r"my deadline|our deadline|"
    r"saved my|saved the day|"
    r"owe[sd]? (him|her|them|me)|"
    r"in return|return the favor|"
    r"scratch my back|you scratch|"
    r"promised|will nominate|going to nominate|"
    r"nominate (you|him|her|them) (next|back|in return)|"
    r"my project|my task|my work"
    r")\b",
    re.IGNORECASE,
)


def detect_transactional(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
    min_hits: int = 2,
) -> list[dict]:
    """
    Nominations whose description text contains ≥ min_hits transactional
    phrases (personal-benefit or quid-pro-quo language).
    """
    findings: list[dict] = []

    for nom in nominations:
        desc = nom.get("Description") or ""
        hits = _TRANSACTIONAL_PATTERNS.findall(desc)
        if len(hits) >= min_hits:
            total_amount = nom["Amount"] or 0
            severity = "High" if len(hits) >= 4 else "Medium"
            findings.append(_finding(
                tenant_id, run_id, "TransactionalLanguage", severity,
                [nom["NominatorId"], nom["BeneficiaryId"]],
                [nom["NominationId"]],
                f"Description contains {len(hits)} transactional phrase(s): "
                f"{', '.join(repr(h) for h in hits[:5])} "
                f"(approved/paid: ${total_amount:,})",
                total_amount=total_amount,
            ))

    logger.info("  TransactionalLanguage: %d detected", len(findings))
    return findings


# ── Pattern 7: Hidden candidate ───────────────────────────────────────────────

def detect_hidden_candidate(
    nominations: list[dict],
    users: list[dict],
    tenant_id: int,
    run_id: str,
    min_text_mentions: int = 5,
) -> list[dict]:
    """
    Users whose full name appears frequently in nomination description text
    but who never appear as a BeneficiaryId — suggesting they are being
    benefited informally without being formally nominated.

    Only active users (those who appear at least once as NominatorId or
    BeneficiaryId) are considered as candidates, to avoid matching
    ex-employees mentioned in historical text.
    """
    active_user_ids = set()
    for nom in nominations:
        active_user_ids.add(nom["NominatorId"])
        active_user_ids.add(nom["BeneficiaryId"])

    beneficiaries = {nom["BeneficiaryId"] for nom in nominations}
    all_text = " ".join(
        (nom.get("Description") or "") for nom in nominations
    ).lower()

    # Build name → user_id map for active users not already a beneficiary
    name_map = {
        user["FullName"].lower(): user["UserId"]
        for user in users
        if user["UserId"] in active_user_ids
        and user["UserId"] not in beneficiaries
        and len(user["FullName"].strip()) > 3
    }

    findings: list[dict] = []
    for name, user_id in name_map.items():
        count = all_text.count(name)
        if count >= min_text_mentions:
            severity = "Medium" if count < 10 else "High"
            findings.append(_finding(
                tenant_id, run_id, "HiddenCandidate", severity,
                [user_id], [],
                f"User {user_id} ('{name}') mentioned {count}× in nomination "
                "descriptions but never appears as a formal BeneficiaryId.",
            ))

    logger.info("  HiddenCandidate: %d detected", len(findings))
    return findings


# ── UserGraphFlags / ApproverPairFlags snapshot ───────────────────────────────

def _has_user_graph_flags(conn: pyodbc.Connection, tenant_id: int) -> bool:
    """Return True if at least one UserGraphFlags snapshot already exists for this tenant."""
    cur = conn.cursor()
    cur.execute(
        "SELECT TOP 1 1 FROM dbo.UserGraphFlags WHERE TenantId = ?", tenant_id
    )
    return cur.fetchone() is not None


def _load_all_findings_for_snapshot(
    conn: pyodbc.Connection,
    tenant_id: int,
    table: str,
) -> list[dict]:
    """
    Load all findings from GraphPatternFindings for this tenant.

    Used ONCE as a bootstrap when UserGraphFlags has no rows for the tenant —
    i.e. the first time graph_pattern_detector runs after migration 0028.
    Subsequent runs use the in-memory detected_findings list (pre-dedup,
    window-bounded) so the snapshot never needs a full table scan.
    """
    cur = conn.cursor()
    cur.execute(f"""
        SELECT PatternType, Severity, AffectedUsers, NominationIds
        FROM   {table}
        WHERE  TenantId      = ?
          AND  AffectedUsers IS NOT NULL
          AND  AffectedUsers <> '[]'
    """, tenant_id)
    return [
        {
            "PatternType":   row[0],
            "Severity":      row[1],
            "AffectedUsers": row[2],
            "NominationIds": row[3] or "[]",
        }
        for row in cur.fetchall()
    ]


def _populate_graph_flag_snapshots(
    conn: pyodbc.Connection,
    tenant_id: int,
    findings: list[dict],
    nominations: list[dict],
    as_of_date: str,
) -> None:
    """
    Materialise graph flag snapshots into dbo.UserGraphFlags and
    dbo.ApproverPairFlags for the RF to read at training and inference time.

    `findings` is the caller's choice of source:
      • Bootstrap (first run after migration): all findings from GraphPatternFindings
        loaded by _load_all_findings_for_snapshot() — covers full history.
      • Normal weekly run: detected_findings (pre-dedup, window-bounded) — cost
        stays proportional to the detection window, never grows with the table.

    UserGraphFlags — one row per (TenantId, UserId, AsOfDate).
      Built by scanning `findings` in memory. MERGE is idempotent if rerun on
      the same AsOfDate.

    ApproverPairFlags — one row per (TenantId, ApproverId, NominatorId,
      BeneficiaryId, AsOfDate).
      Computed from the approved/paid `nominations` list: count how many times
      each (approver, nominator, beneficiary) triple appears.
    """
    cur = conn.cursor()

    # ── Build per-user flag aggregates from in-memory findings ────────────────
    # Each finding's AffectedUsers is a JSON list of user IDs.
    # We accumulate flags per user across all pattern types.

    from collections import defaultdict

    _SEVERITY_RANK = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}

    user_flags: dict[int, dict] = defaultdict(lambda: {
        "IsInRing":                 0,
        "RingMaxUserCount":         0,
        "RingMaxNominationCount":   0,
        "IsSuperNominator":         0,
        "IsInCopyPasteCluster":     0,
        "CopyPasteClusterSize":     0,
        "HasTransactionalLanguage": 0,
        "IsApproverAffinity":       0,
        "HighestSeverity":          None,
        "_severity_rank":           0,
    })

    def _update_severity(flags: dict, severity: str) -> None:
        rank = _SEVERITY_RANK.get(severity, 0)
        if rank > flags["_severity_rank"]:
            flags["_severity_rank"] = rank
            flags["HighestSeverity"] = severity

    for f in findings:
        ptype    = f["PatternType"]
        severity = f["Severity"]
        users    = json.loads(f["AffectedUsers"])
        nom_ids  = json.loads(f.get("NominationIds") or "[]")

        for uid in users:
            uf = user_flags[uid]
            _update_severity(uf, severity)

            if ptype == "Ring":
                uf["IsInRing"] = 1
                uf["RingMaxUserCount"] = max(uf["RingMaxUserCount"], len(users))
                uf["RingMaxNominationCount"] = max(
                    uf["RingMaxNominationCount"], len(nom_ids)
                )
            elif ptype == "SuperNominator":
                uf["IsSuperNominator"] = 1
            elif ptype == "CopyPaste":
                uf["IsInCopyPasteCluster"] = 1
                uf["CopyPasteClusterSize"] = max(
                    uf["CopyPasteClusterSize"], len(nom_ids)
                )
            elif ptype == "TransactionalLanguage":
                uf["HasTransactionalLanguage"] = 1
            elif ptype == "ApproverAffinity":
                uf["IsApproverAffinity"] = 1

    if user_flags:
        rows_ugf = [
            (
                tenant_id,
                uid,
                as_of_date,
                uf["IsInRing"],
                uf["RingMaxUserCount"],
                uf["RingMaxNominationCount"],
                uf["IsSuperNominator"],
                uf["IsInCopyPasteCluster"],
                uf["CopyPasteClusterSize"],
                uf["HasTransactionalLanguage"],
                uf["IsApproverAffinity"],
                uf["HighestSeverity"],
            )
            for uid, uf in user_flags.items()
        ]

        cur.executemany("""
            MERGE dbo.UserGraphFlags AS target
            USING (VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?))
                  AS source (TenantId, UserId, AsOfDate,
                             IsInRing, RingMaxUserCount, RingMaxNominationCount,
                             IsSuperNominator,
                             IsInCopyPasteCluster, CopyPasteClusterSize,
                             HasTransactionalLanguage,
                             IsApproverAffinity, HighestSeverity)
            ON  target.TenantId = source.TenantId
            AND target.UserId   = source.UserId
            AND target.AsOfDate = source.AsOfDate
            WHEN MATCHED THEN
                UPDATE SET
                    IsInRing                 = source.IsInRing,
                    RingMaxUserCount         = source.RingMaxUserCount,
                    RingMaxNominationCount   = source.RingMaxNominationCount,
                    IsSuperNominator         = source.IsSuperNominator,
                    IsInCopyPasteCluster     = source.IsInCopyPasteCluster,
                    CopyPasteClusterSize     = source.CopyPasteClusterSize,
                    HasTransactionalLanguage = source.HasTransactionalLanguage,
                    IsApproverAffinity       = source.IsApproverAffinity,
                    HighestSeverity          = source.HighestSeverity,
                    LastUpdatedUtc           = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (TenantId, UserId, AsOfDate,
                        IsInRing, RingMaxUserCount, RingMaxNominationCount,
                        IsSuperNominator,
                        IsInCopyPasteCluster, CopyPasteClusterSize,
                        HasTransactionalLanguage,
                        IsApproverAffinity, HighestSeverity)
                VALUES (source.TenantId, source.UserId, source.AsOfDate,
                        source.IsInRing, source.RingMaxUserCount,
                        source.RingMaxNominationCount,
                        source.IsSuperNominator,
                        source.IsInCopyPasteCluster, source.CopyPasteClusterSize,
                        source.HasTransactionalLanguage,
                        source.IsApproverAffinity, source.HighestSeverity);
        """, rows_ugf)

        logger.info(
            "  UserGraphFlags: upserted %d user snapshot(s) for AsOfDate=%s",
            len(rows_ugf), as_of_date,
        )

    # ── Build ApproverPairFlags from nomination history ───────────────────────
    # Count (approver, nominator, beneficiary) triples from Approved/Paid noms.
    pair_counts: dict[tuple, int] = defaultdict(int)
    for nom in nominations:
        if nom.get("ApproverId") is not None:
            key = (nom["ApproverId"], nom["NominatorId"], nom["BeneficiaryId"])
            pair_counts[key] += 1

    if pair_counts:
        rows_apf = [
            (tenant_id, approver_id, nominator_id, beneficiary_id, as_of_date, count)
            for (approver_id, nominator_id, beneficiary_id), count in pair_counts.items()
        ]

        cur.executemany("""
            MERGE dbo.ApproverPairFlags AS target
            USING (VALUES (?, ?, ?, ?, ?, ?))
                  AS source (TenantId, ApproverId, NominatorId, BeneficiaryId,
                             AsOfDate, PairApprovalCount)
            ON  target.TenantId      = source.TenantId
            AND target.ApproverId    = source.ApproverId
            AND target.NominatorId   = source.NominatorId
            AND target.BeneficiaryId = source.BeneficiaryId
            AND target.AsOfDate      = source.AsOfDate
            WHEN MATCHED THEN
                UPDATE SET PairApprovalCount = source.PairApprovalCount,
                           LastUpdatedUtc    = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (TenantId, ApproverId, NominatorId, BeneficiaryId,
                        AsOfDate, PairApprovalCount)
                VALUES (source.TenantId, source.ApproverId, source.NominatorId,
                        source.BeneficiaryId, source.AsOfDate, source.PairApprovalCount);
        """, rows_apf)

        logger.info(
            "  ApproverPairFlags: upserted %d pair snapshot(s) for AsOfDate=%s",
            len(rows_apf), as_of_date,
        )

    conn.commit()


# ── Main ──────────────────────────────────────────────────────────────────────

def main(tenants_to_process: list | None = None) -> None:
    log_level = os.getenv("LOGGING_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logger.info("graph_pattern_detector — starting")

    findings_table      = os.getenv("GRAPH_FINDINGS_TABLE", "dbo.GraphPatternFindings")
    default_window_days = int(os.getenv("DETECTION_WINDOW_DAYS", "180"))
    ring_max_cluster    = int(os.getenv("RING_MAX_CLUSTER_SIZE", "0"))
    run_id              = str(uuid.uuid4())
    logger.info("RunId: %s", run_id)
    logger.info("Target table: %s", findings_table)
    logger.info("Default detection window: %d days (DETECTION_WINDOW_DAYS env var)", default_window_days)
    logger.info(
        "Ring max cluster size: %s",
        str(ring_max_cluster) if ring_max_cluster > 0 else "unlimited",
    )

    conn = _get_connection()

    # Refresh graph tables from live Nominations / Users
    sync_graph_tables(conn)

    # Evict embeddings that have aged out of the detection window.
    # Uses the global default window — conservative (keeps more rather than
    # fewer) so tenants with longer per-tenant windows are not penalised.
    _evict_stale_embeddings(conn, default_window_days)

    tenants = _load_tenants(conn)
    if tenants_to_process is not None:
        tenants = [t for t in tenants if t in tenants_to_process]
        if not tenants:
            logger.warning("Tenant(s) %s not found in database. Exiting.", tenants_to_process)
            return
    logger.info("Tenants to process: %s", tenants)

    total_findings = 0

    for tenant_id in tenants:
        logger.info("── Tenant %d ──────────────────────────────────────", tenant_id)

        # Per-tenant detection window — falls back to global default if not set
        # in integrity_config.graph_pattern.detection_window_days.
        tenant_config = _load_tenant_integrity_config(conn, tenant_id)
        window_days = int(
            tenant_config.get("graph_pattern", {})
                         .get("detection_window_days", default_window_days)
        )
        if tenant_config.get("graph_pattern", {}).get("detection_window_days"):
            logger.info("  Detection window: %d days (tenant config)", window_days)
        else:
            logger.info("  Detection window: %d days (default)", window_days)

        # Windowed nominations for all detectors except deserts
        nominations = _load_nominations(conn, tenant_id, window_days)
        users       = _load_users(conn, tenant_id)

        # All-time active set for desert detection — unaffected by the window
        ever_active_ids = _load_ever_active_user_ids(conn, tenant_id)

        logger.info(
            "  Nominations (last %d days): %d  |  Users: %d  |  Ever-active: %d",
            window_days, len(nominations), len(users), len(ever_active_ids),
        )
        if not nominations:
            logger.info("  No nominations in window — skipping.")
            continue

        # Load hashes of findings already in the table for this tenant.
        # All seven detectors share this set — a finding produced by any
        # detector is skipped if its hash already exists.
        existing_hashes = _load_existing_hashes(conn, tenant_id, findings_table)
        logger.info("  Existing hashes in table: %d", len(existing_hashes))

        # detected_findings — ALL patterns found this run (pre-dedup).
        # This is what gets passed to _save_findings (which deduplicates
        # internally) AND to _populate_graph_flag_snapshots on normal runs.
        # Keeping it pre-dedup means the snapshot always reflects every pattern
        # currently detectable in the window, not just ones new to the DB.
        detected_findings: list[dict] = []

        detected_findings.extend(detect_rings(nominations, users, tenant_id, run_id, ring_max_cluster))
        detected_findings.extend(detect_super_nominators(nominations, tenant_id, run_id))
        detected_findings.extend(detect_deserts(ever_active_ids, users, tenant_id, run_id))
        detected_findings.extend(detect_approver_affinity(nominations, tenant_id, run_id))
        detected_findings.extend(detect_copy_paste(nominations, tenant_id, run_id, conn))
        detected_findings.extend(detect_transactional(nominations, tenant_id, run_id))
        detected_findings.extend(detect_hidden_candidate(nominations, users, tenant_id, run_id))

        # Persist — dedup against existing_hashes before inserting
        _save_findings(conn, detected_findings, findings_table, existing_hashes)
        total_findings += len(detected_findings)
        logger.info("  Tenant %d total findings: %d", tenant_id, len(detected_findings))

        # ── Snapshot source selection ─────────────────────────────────────────
        # Bootstrap (first run after migration 0028): UserGraphFlags is empty
        #   for this tenant → load all historical findings from GraphPatternFindings
        #   to capture patterns detected in previous runs / wider windows.
        # Normal run: use detected_findings (pre-dedup, window-bounded) — cost
        #   stays proportional to the detection window, never grows with the table.
        as_of_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if not _has_user_graph_flags(conn, tenant_id):
            snapshot_source = _load_all_findings_for_snapshot(
                conn, tenant_id, findings_table
            )
            logger.info(
                "  Bootstrap: no prior UserGraphFlags for tenant %d — "
                "loading %d finding(s) from %s",
                tenant_id, len(snapshot_source), findings_table,
            )
        else:
            snapshot_source = detected_findings
            logger.info(
                "  Snapshot source: %d detected finding(s) (pre-dedup, window-bounded)",
                len(detected_findings),
            )

        _populate_graph_flag_snapshots(
            conn, tenant_id, snapshot_source, nominations, as_of_date
        )

        # Free all tenant-scoped data before loading the next tenant.
        # nominations and users can be large (11 K+ rows); tenant data is
        # never shared across tenants so there is no reason to keep it.
        del nominations, users, detected_findings, snapshot_source
        gc.collect()
        logger.info("  Tenant %d memory freed.", tenant_id)

    conn.close()
    logger.info(
        "graph_pattern_detector — done. RunId=%s  Total findings=%d",
        run_id, total_findings,
    )


if __name__ == "__main__":
    main()
