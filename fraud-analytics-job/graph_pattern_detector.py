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

def _load_nominations(conn: pyodbc.Connection, tenant_id: int) -> list[dict]:
    """Return all nominations for a tenant as a list of dicts."""
    cur = conn.cursor()
    cur.execute("""
        SELECT n.NominationId, n.NominatorId, n.BeneficiaryId,
               n.ApproverId,   n.Status,      n.Amount,
               n.NominationDescription AS Description,  n.NominationDate AS CreatedAt
        FROM   dbo.Nominations n
        JOIN   dbo.Users u ON u.UserId = n.NominatorId
        WHERE  u.TenantId = ?
    """, tenant_id)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _load_users(conn: pyodbc.Connection, tenant_id: int) -> list[dict]:
    """Return all users for a tenant."""
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


def _load_tenants(conn: pyodbc.Connection) -> list[int]:
    cur = conn.cursor()
    cur.execute("SELECT TenantId FROM dbo.Tenants ORDER BY TenantId")
    return [row[0] for row in cur.fetchall()]


# ── Finding helpers ───────────────────────────────────────────────────────────

def _finding(
    tenant_id: int,
    run_id: str,
    pattern_type: str,
    severity: str,
    affected_users: list[int],
    nomination_ids: list[int],
    detail: str,
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
    }


def _save_findings(conn: pyodbc.Connection, findings: list[dict], table: str) -> None:
    if not findings:
        return
    cur = conn.cursor()
    sql = f"""
        INSERT INTO {table}
               (TenantId, PatternType, Severity,
                AffectedUsers, NominationIds, Detail, DetectedAt, RunId)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        )
        for f in findings
    ]
    cur.executemany(sql, rows)
    conn.commit()
    logger.info("  Saved %d findings to %s", len(findings), table)


# ── Pattern 1: Rings ──────────────────────────────────────────────────────────

def detect_rings(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
) -> list[dict]:
    """
    Directed nomination rings of length ≥ 3 using networkx simple_cycles.
    2-cycles (A→B, B→A) are intentionally skipped — these are caught by the
    Random Forest's HasReciprocalNomination feature. We focus on ≥ 3-hop rings
    that the RF cannot see.

    Severity:
      3–4 nodes → Medium
      5–6 nodes → High
      7+  nodes → Critical
    """
    G = nx.DiGraph()
    edge_nominations: dict[tuple, list[int]] = defaultdict(list)

    for nom in nominations:
        src, dst = nom["NominatorId"], nom["BeneficiaryId"]
        G.add_edge(src, dst)
        edge_nominations[(src, dst)].append(nom["NominationId"])

    findings: list[dict] = []

    # networkx simple_cycles can be slow for large graphs; 291 users is fine.
    for cycle in nx.simple_cycles(G):
        if len(cycle) < 3:
            continue  # skip 2-cycles

        size = len(cycle)
        if size <= 4:
            severity = "Medium"
        elif size <= 6:
            severity = "High"
        else:
            severity = "Critical"

        nom_ids: list[int] = []
        for i in range(size):
            src = cycle[i]
            dst = cycle[(i + 1) % size]
            nom_ids.extend(edge_nominations.get((src, dst), []))

        findings.append(_finding(
            tenant_id, run_id, "Ring", severity,
            sorted(cycle), sorted(set(nom_ids)),
            f"Directed nomination ring of length {size}: "
            f"{' → '.join(str(u) for u in cycle)} → {cycle[0]}",
        ))

    logger.info("  Rings: %d detected", len(findings))
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
    out_degree: dict[int, list[int]] = defaultdict(list)
    for nom in nominations:
        out_degree[nom["NominatorId"]].append(nom["NominationId"])

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
            severity = "High" if cnt >= threshold * 1.5 else "Medium"
            findings.append(_finding(
                tenant_id, run_id, "SuperNominator", severity,
                [user_id], nom_ids,
                f"User {user_id} sent {cnt} nominations "
                f"(tenant mean={mean:.1f}, threshold={threshold:.1f})",
            ))

    logger.info("  SuperNominators: %d detected", len(findings))
    return findings


# ── Pattern 3: Nomination deserts ─────────────────────────────────────────────

def detect_deserts(
    nominations: list[dict],
    users: list[dict],
    tenant_id: int,
    run_id: str,
) -> list[dict]:
    """
    Teams (grouped by ManagerId) where no member ever appears on either side
    of a nomination — neither nominator nor beneficiary.
    Minimum team size: 3 members (singletons and pairs excluded).
    """
    all_participants = set()
    for nom in nominations:
        all_participants.add(nom["NominatorId"])
        all_participants.add(nom["BeneficiaryId"])

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

    pair_total:    dict[tuple, int] = defaultdict(int)
    pair_approved: dict[tuple, int] = defaultdict(int)
    pair_noms:     dict[tuple, list[int]] = defaultdict(list)

    for nom in nominations:
        if nom["ApproverId"] is None:
            continue
        key = (nom["NominatorId"], nom["ApproverId"])
        pair_total[key]   += 1
        pair_noms[key].append(nom["NominationId"])
        if nom["Status"] in approved_statuses:
            pair_approved[key] += 1

    findings: list[dict] = []
    for key, cnt in pair_total.items():
        if cnt < 5:
            continue
        rate = pair_approved[key] / cnt
        if rate >= 2 * baseline and baseline > 0:
            nominator_id, approver_id = key
            severity = "High" if rate >= 3 * baseline else "Medium"
            findings.append(_finding(
                tenant_id, run_id, "ApproverAffinity", severity,
                [nominator_id, approver_id], pair_noms[key],
                f"Nominator {nominator_id} / Approver {approver_id}: "
                f"approval rate {rate:.0%} vs tenant baseline {baseline:.0%} "
                f"({cnt} nominations)",
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
        nom_ids  = [eligible[i]["NominationId"] for i in members]
        user_ids = list({eligible[i]["NominatorId"] for i in members})

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
            f"similarity {avg_sim:.3f} (threshold {similarity_threshold})",
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
            severity = "High" if len(hits) >= 4 else "Medium"
            findings.append(_finding(
                tenant_id, run_id, "TransactionalLanguage", severity,
                [nom["NominatorId"], nom["BeneficiaryId"]],
                [nom["NominationId"]],
                f"Description contains {len(hits)} transactional phrase(s): "
                f"{', '.join(repr(h) for h in hits[:5])}",
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

    findings_table = os.getenv("GRAPH_FINDINGS_TABLE", "dbo.GraphPatternFindings")
    run_id = str(uuid.uuid4())
    logger.info("RunId: %s", run_id)
    logger.info("Target table: %s", findings_table)

    conn = _get_connection()

    # Refresh graph tables from live Nominations / Users
    sync_graph_tables(conn)

    tenants = _load_tenants(conn)
    logger.info("Tenants to process: %s", tenants)

    total_findings = 0

    for tenant_id in tenants:
        logger.info("── Tenant %d ──────────────────────────────────────", tenant_id)

        # Load this tenant's data
        nominations = _load_nominations(conn, tenant_id)
        users       = _load_users(conn, tenant_id)

        logger.info("  Nominations: %d  |  Users: %d", len(nominations), len(users))
        if not nominations:
            logger.info("  No nominations — skipping.")
            continue

        tenant_findings: list[dict] = []

        tenant_findings.extend(detect_rings(nominations, tenant_id, run_id))
        tenant_findings.extend(detect_super_nominators(nominations, tenant_id, run_id))
        tenant_findings.extend(detect_deserts(nominations, users, tenant_id, run_id))
        tenant_findings.extend(detect_approver_affinity(nominations, tenant_id, run_id))
        tenant_findings.extend(detect_copy_paste(nominations, tenant_id, run_id))
        tenant_findings.extend(detect_transactional(nominations, tenant_id, run_id))
        tenant_findings.extend(detect_hidden_candidate(nominations, users, tenant_id, run_id))

        # Persist before freeing — findings are small and safe to keep
        _save_findings(conn, tenant_findings, findings_table)
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
