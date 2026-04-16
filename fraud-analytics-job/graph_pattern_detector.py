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

logger = logging.getLogger(__name__)

# ── Database helpers ──────────────────────────────────────────────────────────

def _get_connection() -> pyodbc.Connection:
    server   = os.environ["SQL_SERVER"]
    database = os.environ["SQL_DATABASE"]
    user     = os.environ["SQL_USER"]
    password = os.environ["SQL_PASSWORD"]
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str)


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

            findings.append(_finding(
                tenant_id, run_id, "Ring", severity,
                members, sorted(set(nom_ids)),
                f"Nomination ring of {size} users: "
                f"{' → '.join(str(u) for u in cycle)} → {cycle[0]} "
                f"(total approved/paid: ${total_amount:,})",
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


# ── Pattern 5: Copy-paste fraud ───────────────────────────────────────────────

def detect_copy_paste(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
    similarity_threshold: float = 0.92,
    min_cluster_size: int = 3,
    chunk_size: int = 512,
) -> list[dict]:
    """
    Clusters of nominations whose description embeddings are mutually similar
    (cosine ≥ similarity_threshold). Uses sentence-transformers for embeddings
    and union-find for cluster formation.

    Memory strategy: instead of materialising the full N×N similarity matrix
    (which is ~500 MB at 11 K nominations), we process row-chunks of
    `chunk_size` at a time.  Each chunk produces a (chunk_size × N) slice
    that is discarded after the pairs above the threshold are recorded.
    Peak extra memory per chunk: chunk_size × N × 4 bytes
    = 512 × 11 196 × 4 ≈ 23 MB — manageable inside 4 Gi.

    The sentence-transformers model (~500 MB resident) is explicitly deleted
    and garbage-collected after encoding so the memory is freed before the
    rest of the detection pipeline runs.

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

    texts = [n["Description"] for n in eligible]
    logger.info("  Encoding %d descriptions …", len(texts))

    model = SentenceTransformer("all-MiniLM-L6-v2")
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=64,
        show_progress_bar=False,
    )
    embeddings = np.array(embeddings, dtype=np.float32)

    # Free the ~500 MB PyTorch model immediately after encoding
    del model
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log_level = os.getenv("LOGGING_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logger.info("graph_pattern_detector — starting")

    findings_table    = os.getenv("GRAPH_FINDINGS_TABLE", "dbo.GraphPatternFindings")
    window_days       = int(os.getenv("DETECTION_WINDOW_DAYS", "180"))
    ring_max_cluster  = int(os.getenv("RING_MAX_CLUSTER_SIZE", "0"))
    run_id            = str(uuid.uuid4())
    logger.info("RunId: %s", run_id)
    logger.info("Target table: %s", findings_table)
    logger.info("Detection window: %d days", window_days)
    logger.info(
        "Ring max cluster size: %s",
        str(ring_max_cluster) if ring_max_cluster > 0 else "unlimited",
    )

    conn = _get_connection()

    # Refresh graph tables from live Nominations / Users
    sync_graph_tables(conn)

    tenants = _load_tenants(conn)
    logger.info("Tenants to process: %s", tenants)

    total_findings = 0

    for tenant_id in tenants:
        logger.info("── Tenant %d ──────────────────────────────────────", tenant_id)

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

        tenant_findings: list[dict] = []

        tenant_findings.extend(detect_rings(nominations, tenant_id, run_id, ring_max_cluster))
        tenant_findings.extend(detect_super_nominators(nominations, tenant_id, run_id))
        tenant_findings.extend(detect_deserts(ever_active_ids, users, tenant_id, run_id))
        tenant_findings.extend(detect_approver_affinity(nominations, tenant_id, run_id))
        tenant_findings.extend(detect_copy_paste(nominations, tenant_id, run_id))
        tenant_findings.extend(detect_transactional(nominations, tenant_id, run_id))
        tenant_findings.extend(detect_hidden_candidate(nominations, users, tenant_id, run_id))

        # Persist — dedup against existing_hashes before inserting
        _save_findings(conn, tenant_findings, findings_table, existing_hashes)
        total_findings += len(tenant_findings)
        logger.info("  Tenant %d total findings: %d", tenant_id, len(tenant_findings))

        # Free all tenant-scoped data before loading the next tenant.
        # nominations and users can be large (11 K+ rows); tenant data is
        # never shared across tenants so there is no reason to keep it.
        del nominations, users, tenant_findings
        gc.collect()
        logger.info("  Tenant %d memory freed.", tenant_id)

    conn.close()
    logger.info(
        "graph_pattern_detector — done. RunId=%s  Total findings=%d",
        run_id, total_findings,
    )


if __name__ == "__main__":
    main()
