"""
graph_analytics.py
==================
Stage 1 of the fraud-analytics-job pipeline.

Detects eight structural, temporal, and semantic behavioural patterns in the Nominations
graph for each tenant and upserts findings into dbo.GraphPatternFindings.

Pattern catalogue
-----------------
1. Ring                — directed cycles ≥ 3 hops (networkx simple_cycles)
2. BipartiteDenseBlock — highly overlapping many-to-few or few-to-many groups
3. TemporalBurst       — nomination volume compressed into an anomalous short window
4. SuperNominator      — out-degree distribution outlier
5. SuperBeneficiary    — in-degree distribution outlier with broad support
6. CopyPaste           — cosine similarity ≥ 0.92 between descriptions, min cluster 3
7. HiddenCandidate     — name appears ≥ 5× in descriptions but never a BeneficiaryId
8. Desert              — whole team absent from both sides of the graph

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
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from typing import Any

import networkx as nx
import numpy as np
import pyodbc
from pathlib import Path
from dotenv import load_dotenv

from utils.component_status import upsert_component_status

# Same .env loading as train_rf_model.py / forecast_models.py so this stage
# can be run standalone locally. No-op in Container Apps (env injected).
JOB_DIR = Path(__file__).resolve().parents[1]
env_path = JOB_DIR.parent / ".env"
load_dotenv(env_path)

logger = logging.getLogger(__name__)

_SEVERITY_SCORE = {"Low": 25.0, "Medium": 50.0, "High": 75.0, "Critical": 100.0}


def _risk_level(score: float, thresholds: dict[str, float]) -> str:
    if score >= thresholds["critical"]:
        return "Critical"
    if score >= thresholds["high"]:
        return "High"
    if score >= thresholds["medium"]:
        return "Medium"
    if score >= thresholds["low"]:
        return "Low"
    return "None"


def _pattern_config(policy: dict | None, pattern_type: str) -> dict:
    if not policy:
        return {}
    return (policy.get("patterns") or {}).get(pattern_type, {})


def _continuous_score(
    policy: dict,
    pattern_type: str,
    signals: dict[str, float],
) -> tuple[float, str, dict]:
    """Score one finding from normalized 0..1 signals and policy weights."""
    pattern = _pattern_config(policy, pattern_type)
    parameters = pattern.get("parameters") or {}
    base = float(pattern.get("base_score", 0))
    contributions: dict[str, float] = {}
    for name, raw_value in signals.items():
        value = max(0.0, min(1.0, float(raw_value)))
        weight = float(parameters.get(f"{name}_weight", 0))
        contributions[name] = round(value * weight, 4)
    minimum = float(pattern.get("minimum_score", 0))
    maximum = float(pattern.get("maximum_score", 100))
    score = round(max(minimum, min(maximum, base + sum(contributions.values()))), 2)
    severity = _risk_level(score, policy["thresholds"])
    return score, severity, {
        "base_score": base,
        "signals": {key: round(max(0.0, min(1.0, float(value))), 4)
                    for key, value in signals.items()},
        "weights": {key: float(parameters.get(f"{key}_weight", 0))
                    for key in signals},
        "contributions": contributions,
        "finding_score": score,
    }


def _load_active_graph_policy(
    conn: pyodbc.Connection,
    tenant_id: int,
    default_window_days: int,
) -> dict:
    """Load the immutable active scoring policy and its detector parameters."""
    cur = conn.cursor()
    cur.execute("""
        SELECT TOP 1 PolicyId, PolicyVersion, ScoringStrategy,
               LowThreshold, MediumThreshold, HighThreshold, CriticalThreshold,
               DetectionWindowDays, SnapshotMaxAgeDays
        FROM dbo.GraphScoringPolicies
        WHERE TenantId = ? AND Status = 'ACTIVE'
        ORDER BY PolicyVersion DESC
    """, tenant_id)
    row = cur.fetchone()
    if not row:
        raise RuntimeError(
            f"Tenant {tenant_id} has no active Graph Analytics scoring policy"
        )
    policy = {
        "policy_id": int(row[0]),
        "version": int(row[1]),
        "strategy": str(row[2]),
        "thresholds": {
            "low": float(row[3]), "medium": float(row[4]),
            "high": float(row[5]), "critical": float(row[6]),
        },
        "detection_window_days": int(row[7] or default_window_days),
        "snapshot_max_age_days": int(row[8] or 14),
        "patterns": {},
    }
    cur.execute("""
        SELECT PatternType, Enabled, EnabledForRouting, ApplicableRolesJson,
               BaseScore, MinimumScore, MaximumScore, ParametersJson
        FROM dbo.GraphScoringPatternParameters
        WHERE PolicyId = ?
    """, policy["policy_id"])
    for item in cur.fetchall():
        try:
            roles = json.loads(item[3]) if item[3] else []
            parameters = json.loads(item[7]) if item[7] else {}
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(
                f"Invalid Graph policy JSON for tenant {tenant_id}, pattern {item[0]}"
            ) from exc
        policy["patterns"][str(item[0])] = {
            "enabled": bool(item[1]),
            "enabled_for_routing": bool(item[2]),
            "applicable_roles": roles,
            "base_score": float(item[4]),
            "minimum_score": float(item[5]),
            "maximum_score": float(item[6]),
            "parameters": parameters,
        }
    return policy

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


from utils.db_conn import connect  # noqa: E402 - .env must load before credential setup


def _get_connection() -> pyodbc.Connection:
    """Connect to Azure SQL via Managed Identity (see utils.db_conn.connect)."""
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
    Return in-scope P2P nominations within the rolling detection window.

    Pending, Approved, and Paid represent nomination behavior that has passed
    the integrity submission stage. Rejected rows are excluded from detector
    topology; HRBP-confirmed rejected outcomes remain available separately as
    supervised model labels.

    window_days controls how far back to look. All active detectors share
    this tenant-policy value.

    Set DETECTION_WINDOW_DAYS=3650 on first deploy to process full history.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT n.NominationId, n.NominatorId, n.BeneficiaryId,
               n.Status,       n.Amount,
               n.NominationDescription AS Description,  n.NominationDate AS CreatedAt
        FROM   dbo.Nominations n
        JOIN   dbo.Users u ON u.UserId = n.NominatorId
        WHERE  u.TenantId = ?
          AND  n.NominationDate >= DATEADD(DAY, -?, GETDATE())
          AND  n.Status IN ('Pending', 'Approved', 'Paid')
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


def _maximum_active_detection_window(
    conn: pyodbc.Connection,
    fallback_days: int,
) -> int:
    """Preserve embeddings needed by the tenant with the longest active policy."""
    cur = conn.cursor()
    cur.execute("""
        SELECT MAX(DetectionWindowDays)
        FROM dbo.GraphScoringPolicies
        WHERE Status = 'ACTIVE'
    """)
    row = cur.fetchone()
    return max(int(row[0] or fallback_days), fallback_days)


# ── Finding helpers ───────────────────────────────────────────────────────────

def _fingerprint(
    tenant_id: int,
    pattern_type: str,
    affected_users: list[int],   # must already be sorted
    nomination_ids: list[int],   # must already be sorted
    policy_version: int | None = None,
) -> str:
    """
    Deterministic SHA-256 fingerprint (64 hex chars) of a finding's content.

    Inputs must be pre-sorted so the hash is stable regardless of detection
    order.  The fingerprint is stored in FindingHash and used to prevent
    duplicate inserts across runs.

    Same content → same hash → not re-inserted (idempotent).
    Evolved content (e.g. new nominations added to a ring) → new hash → inserted.
    """
    key = (
        f"{tenant_id}|{pattern_type}|{json.dumps(affected_users)}|"
        f"{json.dumps(nomination_ids)}|policy:{policy_version or 'legacy'}"
    )
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
    *,
    policy: dict | None = None,
    signals: dict[str, float] | None = None,
) -> dict[str, Any]:
    if policy:
        score, severity, score_components = _continuous_score(
            policy, pattern_type, signals or {}
        )
        policy_version = int(policy["version"])
        pattern = _pattern_config(policy, pattern_type)
    else:
        score = _SEVERITY_SCORE.get(severity, 0.0)
        score_components = {
            "legacy_severity_mapping": True,
            "finding_score": score,
        }
        policy_version = None
        pattern = {}
    return {
        "TenantId":      tenant_id,
        "PatternType":   pattern_type,
        "Severity":      severity,
        "AffectedUsers": json.dumps(affected_users),
        "NominationIds": json.dumps(nomination_ids),
        "Detail":        detail[:1000],
        "DetectedAt":    datetime.now(timezone.utc),
        "RunId":         run_id,
        "FindingHash":   _fingerprint(
            tenant_id, pattern_type, affected_users, nomination_ids, policy_version
        ),
        "TotalAmount":   total_amount,
        "FindingScore":  score,
        "ScoringPolicyVersion": policy_version,
        "ScoreComponentsJson": json.dumps(score_components, separators=(",", ":")),
        "EnabledForRouting": bool(pattern.get("enabled_for_routing", True)),
        "ApplicableRoles": list(pattern.get(
            "applicable_roles", ["nominator", "beneficiary"]
        )),
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
                TotalAmount, FindingScore, ScoringPolicyVersion, ScoreComponentsJson)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            f.get("FindingScore"),
            f.get("ScoringPolicyVersion"),
            f.get("ScoreComponentsJson"),
        )
        for f in new_findings
    ]
    cur.executemany(sql, rows)
    conn.commit()
    logger.info("  Saved %d new finding(s) to %s.", len(new_findings), table)


# ── Rings ─────────────────────────────────────────────────────────────────────

def detect_rings(
    nominations: list[dict],
    users: list[dict],
    tenant_id: int,
    run_id: str,
    max_cluster_size: int = 0,
    policy: dict | None = None,
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

    Severity — nominated amount across the in-scope P2P population:
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

            # Severity uses total nominated amount as an exposure proxy. Pending
            # amounts are potential rather than committed exposure.
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
                f"(Total nominated amount: ${total_amount:,})",
                total_amount=total_amount,
                policy=policy,
                signals={
                    "exposure": min(total_amount / max(float(
                        _pattern_config(policy, "Ring").get("parameters", {})
                        .get("amount_reference", 10_000)
                    ), 1.0), 1.0),
                    "repeat": min(len(nom_ids) / max(size * 3, 1), 1.0),
                    "compactness": max(0.0, 1.0 - ((size - 3) / 5.0)),
                },
            ))

    logger.info(
        "  Rings: %d detected (sizes 3–%d, frozenset dedup).",
        len(findings), upper,
    )
    return findings


# ── Super-nominators ──────────────────────────────────────────────────────────

def detect_super_nominators(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
    policy: dict | None = None,
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

    parameters = _pattern_config(policy, "SuperNominator").get("parameters", {})
    threshold = max(
        mean + float(parameters.get("standard_deviations", 2.0)) * std,
        float(parameters.get("median_multiplier", 3.0)) * median,
        float(parameters.get("minimum_count", 5)),
    )

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
                f"total nominated amount: ${total_amount:,})",
                total_amount=total_amount,
                policy=policy,
                signals={
                    "excess": min(max((cnt / max(threshold, 1)) - 1.0, 0.0), 1.0),
                    "volume": min(cnt / max(threshold * 2.0, 1.0), 1.0),
                    "exposure": min(total_amount / max(float(
                        parameters.get("amount_reference", 10_000)
                    ), 1.0), 1.0),
                },
            ))

    logger.info("  SuperNominators: %d detected", len(findings))
    return findings


def _as_date(value: Any) -> date | None:
    """Normalize SQL, Python, and ISO timestamp values for temporal detectors."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


# ── Super beneficiaries ───────────────────────────────────────────────────────

def detect_super_beneficiaries(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
    policy: dict | None = None,
) -> list[dict]:
    """Find unusually frequent beneficiaries supported by several nominators."""
    incoming: dict[int, list[dict]] = defaultdict(list)
    for nomination in nominations:
        incoming[nomination["BeneficiaryId"]].append(nomination)
    if len(incoming) < 3:
        return []

    counts = np.array([len(items) for items in incoming.values()], dtype=float)
    mean = float(counts.mean())
    std = float(counts.std())
    median = float(np.median(counts))
    parameters = _pattern_config(policy, "SuperBeneficiary").get("parameters", {})
    threshold = max(
        mean + float(parameters.get("standard_deviations", 2.0)) * std,
        float(parameters.get("median_multiplier", 3.0)) * median,
        float(parameters.get("minimum_count", 5)),
    )
    minimum_unique = int(parameters.get("minimum_unique_nominators", 4))
    unique_reference = max(float(parameters.get("unique_reference", 10)), 1.0)
    compactness_reference = max(
        float(parameters.get("compactness_reference_days", 14)), 1.0
    )

    findings: list[dict] = []
    for beneficiary_id, items in incoming.items():
        count = len(items)
        nominators = [int(item["NominatorId"]) for item in items]
        unique_nominators = len(set(nominators))
        if count < threshold or unique_nominators < minimum_unique:
            continue

        dates = [
            parsed for parsed in (_as_date(item.get("CreatedAt")) for item in items)
            if parsed is not None
        ]
        span_days = (
            (max(dates) - min(dates)).days + 1 if dates else compactness_reference + 1
        )
        total_amount = sum(float(item.get("Amount") or 0) for item in items)
        nomination_ids = sorted({int(item["NominationId"]) for item in items})
        dominant_count = max(Counter(nominators).values())
        concentration_floor = 1.0 / unique_nominators
        dominant_share = dominant_count / count
        repeat_concentration = max(
            0.0,
            min(
                (dominant_share - concentration_floor)
                / max(1.0 - concentration_floor, 0.001),
                1.0,
            ),
        )
        severity = "High" if count >= threshold * 1.5 else "Medium"
        findings.append(_finding(
            tenant_id, run_id, "SuperBeneficiary", severity,
            [beneficiary_id], nomination_ids,
            f"User {beneficiary_id} received {count} nominations from "
            f"{unique_nominators} distinct nominators "
            f"(tenant mean={mean:.1f}, threshold={threshold:.1f}, "
            f"activity span={span_days:.0f} day(s), "
            f"total nominated amount: USD {total_amount:,.0f})",
            total_amount=total_amount,
            policy=policy,
            signals={
                "excess": min(max((count / max(threshold, 1)) - 1.0, 0.0), 1.0),
                "breadth": min(unique_nominators / unique_reference, 1.0),
                "repeat_concentration": repeat_concentration,
                "compactness": max(
                    0.0, 1.0 - ((span_days - 1) / compactness_reference)
                ),
                "exposure": min(total_amount / max(float(
                    parameters.get("amount_reference", 10_000)
                ), 1.0), 1.0),
            },
        ))

    logger.info("  SuperBeneficiaries: %d detected", len(findings))
    return findings


# ── Temporal bursts ───────────────────────────────────────────────────────────

def detect_temporal_bursts(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
    policy: dict | None = None,
) -> list[dict]:
    """Find non-overlapping short windows with anomalous nomination volume."""
    dated = [
        (parsed, nomination)
        for nomination in nominations
        if (parsed := _as_date(nomination.get("CreatedAt"))) is not None
    ]
    if not dated:
        return []

    parameters = _pattern_config(policy, "TemporalBurst").get("parameters", {})
    burst_days = max(int(parameters.get("burst_window_days", 3)), 1)
    baseline_days = max(int(parameters.get("minimum_baseline_days", 21)), burst_days)
    minimum_count = max(int(parameters.get("minimum_nominations", 8)), 1)
    standard_deviations = float(parameters.get("standard_deviations", 3.0))
    overlap_suppression = float(parameters.get("overlap_suppression", 0.6))

    first_date = min(item[0] for item in dated)
    last_date = max(item[0] for item in dated)
    observed_days = (last_date - first_date).days + 1
    if observed_days < baseline_days:
        return []

    by_date: dict[date, list[dict]] = defaultdict(list)
    for nomination_date, nomination in dated:
        by_date[nomination_date].append(nomination)

    starts = [
        first_date + timedelta(days=offset)
        for offset in range(max(observed_days - burst_days + 1, 1))
    ]
    rolling_counts = np.array([
        sum(
            len(by_date.get(start + timedelta(days=offset), []))
            for offset in range(burst_days)
        )
        for start in starts
    ], dtype=float)
    expected = float(np.median(rolling_counts))
    median_absolute_deviation = float(
        np.median(np.abs(rolling_counts - expected))
    )
    robust_deviation = max(
        1.4826 * median_absolute_deviation,
        float(np.sqrt(max(expected, 1.0))),
    )
    threshold = max(
        minimum_count,
        expected + standard_deviations * robust_deviation,
    )

    candidates: list[tuple[int, date, list[dict]]] = []
    for start, count_value in zip(starts, rolling_counts):
        count = int(count_value)
        if count < threshold:
            continue
        items = [
            nomination
            for offset in range(burst_days)
            for nomination in by_date.get(start + timedelta(days=offset), [])
        ]
        candidates.append((count, start, items))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    accepted_sets: list[set[int]] = []
    findings: list[dict] = []
    for count, start, items in candidates:
        nomination_ids = {int(item["NominationId"]) for item in items}
        if any(
            len(nomination_ids & existing)
            / max(min(len(nomination_ids), len(existing)), 1)
            >= overlap_suppression
            for existing in accepted_sets
        ):
            continue
        accepted_sets.append(nomination_ids)

        nominators = [int(item["NominatorId"]) for item in items]
        beneficiaries = [int(item["BeneficiaryId"]) for item in items]
        affected_users = sorted(set(nominators) | set(beneficiaries))
        total_amount = sum(float(item.get("Amount") or 0) for item in items)
        daily_peak = max(
            len(by_date.get(start + timedelta(days=offset), []))
            for offset in range(burst_days)
        )
        dominant_participant_count = max(
            max(Counter(nominators).values()),
            max(Counter(beneficiaries).values()),
        )
        participant_concentration = dominant_participant_count / count
        end = start + timedelta(days=burst_days - 1)
        severity = "High" if count >= threshold * 1.5 else "Medium"
        findings.append(_finding(
            tenant_id, run_id, "TemporalBurst", severity,
            affected_users, sorted(nomination_ids),
            f"{count} nominations occurred from {start.isoformat()} through "
            f"{end.isoformat()} (expected rolling count={expected:.1f}, "
            f"threshold={threshold:.1f}, {len(set(nominators))} nominators, "
            f"{len(set(beneficiaries))} beneficiaries, "
            f"total nominated amount: USD {total_amount:,.0f})",
            total_amount=total_amount,
            policy=policy,
            signals={
                "excess": min(max((count / max(threshold, 1)) - 1.0, 0.0), 1.0),
                "volume": min(count / max(float(
                    parameters.get("count_reference", 20)
                ), 1.0), 1.0),
                "participant_concentration": participant_concentration,
                "temporal_compactness": min(daily_peak / max(count, 1), 1.0),
                "exposure": min(total_amount / max(float(
                    parameters.get("amount_reference", 10_000)
                ), 1.0), 1.0),
            },
        ))

    logger.info("  TemporalBursts: %d detected", len(findings))
    return findings


def _average_pairwise_jaccard(
    members: set[int],
    neighbors: dict[int, set[int]],
) -> float:
    values = []
    for left, right in combinations(sorted(members), 2):
        union = neighbors[left] | neighbors[right]
        if union:
            values.append(len(neighbors[left] & neighbors[right]) / len(union))
    return float(np.mean(values)) if values else 0.0


def _overlap_components(
    neighbors: dict[int, set[int]],
    minimum_shared: int,
    similarity_threshold: float,
) -> list[set[int]]:
    """Generate bounded dense-block candidates from neighbor-set overlap."""
    graph = nx.Graph()
    eligible = [
        member for member, values in neighbors.items()
        if len(values) >= minimum_shared
    ]
    graph.add_nodes_from(eligible)
    for left, right in combinations(eligible, 2):
        intersection = neighbors[left] & neighbors[right]
        union = neighbors[left] | neighbors[right]
        similarity = len(intersection) / len(union) if union else 0.0
        if len(intersection) >= minimum_shared and similarity >= similarity_threshold:
            graph.add_edge(left, right)
    return [
        set(component) for component in nx.connected_components(graph)
        if len(component) >= 2
    ]


def _dense_neighbor_core(
    members: set[int],
    neighbors: dict[int, set[int]],
    minimum_density: float,
) -> set[int]:
    """Remove incidental neighbors that are not shared by most block members."""
    counts: dict[int, int] = defaultdict(int)
    for member in members:
        for neighbor in neighbors[member]:
            counts[neighbor] += 1
    return {
        neighbor for neighbor, count in counts.items()
        if count / max(len(members), 1) >= minimum_density
    }


# ── Bipartite dense blocks ────────────────────────────────────────────────────

def detect_bipartite_dense_blocks(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
    policy: dict | None = None,
) -> list[dict]:
    """Detect dense, overlapping nominator-to-beneficiary campaign blocks."""
    outgoing: dict[int, set[int]] = defaultdict(set)
    incoming: dict[int, set[int]] = defaultdict(set)
    edge_items: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for nomination in nominations:
        left = int(nomination["NominatorId"])
        right = int(nomination["BeneficiaryId"])
        outgoing[left].add(right)
        incoming[right].add(left)
        edge_items[(left, right)].append(nomination)

    parameters = _pattern_config(policy, "BipartiteDenseBlock").get(
        "parameters", {}
    )
    minimum_side = max(int(parameters.get("minimum_side_size", 2)), 2)
    minimum_large_side = max(
        int(parameters.get("minimum_large_side_size", 3)), minimum_side
    )
    minimum_shared = max(int(parameters.get("minimum_shared_neighbors", 2)), 1)
    similarity_threshold = float(parameters.get("overlap_threshold", 0.6))
    minimum_density = float(parameters.get("minimum_density", 0.65))
    minimum_edges = max(int(parameters.get("minimum_edges", 6)), 1)

    candidate_keys: set[tuple[frozenset[int], frozenset[int]]] = set()
    for left_group in _overlap_components(
        outgoing, minimum_shared, similarity_threshold
    ):
        right_group = _dense_neighbor_core(
            left_group, outgoing, minimum_density
        )
        candidate_keys.add((frozenset(left_group), frozenset(right_group)))
    for right_group in _overlap_components(
        incoming, minimum_shared, similarity_threshold
    ):
        left_group = _dense_neighbor_core(
            right_group, incoming, minimum_density
        )
        candidate_keys.add((frozenset(left_group), frozenset(right_group)))

    candidate_records: list[dict] = []
    for frozen_left, frozen_right in candidate_keys:
        left_group, right_group = set(frozen_left), set(frozen_right)
        if (
            len(left_group) < minimum_side
            or len(right_group) < minimum_side
            or max(len(left_group), len(right_group)) < minimum_large_side
        ):
            continue
        internal_edges = {
            (left, right)
            for left in left_group for right in right_group
            if (left, right) in edge_items
        }
        density = len(internal_edges) / max(
            len(left_group) * len(right_group), 1
        )
        if len(internal_edges) < minimum_edges or density < minimum_density:
            continue
        items = [
            item for edge in internal_edges for item in edge_items[edge]
        ]
        candidate_records.append({
            "left": left_group,
            "right": right_group,
            "edges": internal_edges,
            "items": items,
            "density": density,
        })

    candidate_records.sort(
        key=lambda item: (-item["density"], -len(item["edges"]))
    )
    accepted_edges: list[set[tuple[int, int]]] = []
    findings: list[dict] = []
    for candidate in candidate_records:
        internal_edges = candidate["edges"]
        if any(
            len(internal_edges & existing)
            / max(min(len(internal_edges), len(existing)), 1) >= 0.8
            for existing in accepted_edges
        ):
            continue
        accepted_edges.append(internal_edges)

        left_group = candidate["left"]
        right_group = candidate["right"]
        items = candidate["items"]
        nomination_ids = sorted({int(item["NominationId"]) for item in items})
        total_amount = sum(float(item.get("Amount") or 0) for item in items)
        overlap = max(
            _average_pairwise_jaccard(left_group, outgoing),
            _average_pairwise_jaccard(right_group, incoming),
        )
        exclusivity_values = [
            len(outgoing[user] & right_group) / max(len(outgoing[user]), 1)
            for user in left_group
        ] + [
            len(incoming[user] & left_group) / max(len(incoming[user]), 1)
            for user in right_group
        ]
        exclusivity = float(np.mean(exclusivity_values))
        repeat_rate = max(
            (len(items) / max(len(internal_edges), 1)) - 1.0, 0.0
        )
        dates = [
            parsed for parsed in (_as_date(item.get("CreatedAt")) for item in items)
            if parsed is not None
        ]
        span_days = (max(dates) - min(dates)).days + 1 if dates else 0
        compactness_reference = max(float(
            parameters.get("compactness_reference_days", 14)
        ), 1.0)
        density = float(candidate["density"])
        severity = "High" if density >= 0.85 else "Medium"
        findings.append(_finding(
            tenant_id, run_id, "BipartiteDenseBlock", severity,
            sorted(left_group | right_group), nomination_ids,
            f"Dense nomination block with {len(left_group)} nominators, "
            f"{len(right_group)} beneficiaries, {len(internal_edges)} distinct "
            f"edges, density={density:.3f}, overlap={overlap:.3f}, "
            f"activity span={span_days} day(s), "
            f"total nominated amount: USD {total_amount:,.0f}",
            total_amount=total_amount,
            policy=policy,
            signals={
                "density": min(max(
                    (density - minimum_density)
                    / max(1.0 - minimum_density, 0.001), 0.0
                ), 1.0),
                "overlap": min(overlap, 1.0),
                "exclusivity": min(exclusivity, 1.0),
                "repeat": min(repeat_rate / max(float(
                    parameters.get("repeat_reference", 2)
                ), 1.0), 1.0),
                "compactness": (
                    max(0.0, 1.0 - ((span_days - 1) / compactness_reference))
                    if span_days else 0.0
                ),
                "exposure": min(total_amount / max(float(
                    parameters.get("amount_reference", 10_000)
                ), 1.0), 1.0),
            },
        ))

    logger.info("  BipartiteDenseBlocks: %d detected", len(findings))
    return findings


# ── Nomination deserts ────────────────────────────────────────────────────────

def detect_deserts(
    ever_active_ids: set[int],
    users: list[dict],
    tenant_id: int,
    run_id: str,
    policy: dict | None = None,
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

    parameters = _pattern_config(policy, "Desert").get("parameters", {})
    minimum_team_size = int(parameters.get("minimum_team_size", 3))
    team_reference = max(float(parameters.get("team_size_reference", 10)), 1.0)
    findings: list[dict] = []
    for manager_id, members in teams.items():
        if len(members) < minimum_team_size:
            continue
        absent = [m for m in members if m not in all_participants]
        if len(absent) == len(members):  # entire team is absent
            findings.append(_finding(
                tenant_id, run_id, "Desert", "Medium",
                members, [],
                f"Team under manager {manager_id} ({len(members)} members) "
                "has zero nomination activity on either side.",
                policy=policy,
                signals={"team_size": min(len(members) / team_reference, 1.0)},
            ))

    logger.info("  Deserts: %d detected", len(findings))
    return findings


# ── Embedding cache helpers ───────────────────────────────────────────────────

def _evict_stale_embeddings(conn: pyodbc.Connection, window_days: int) -> None:
    """
    Delete cached embeddings for nominations no longer within the active
    detection window or in-scope P2P population.

    Called once per job run — before per-tenant processing — to keep the
    NomGraph_NominationEmbedding table bounded to roughly
    DETECTION_WINDOW_DAYS × eligible nomination rate rows.
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
              AND  n.Status         IN ('Pending', 'Approved', 'Paid')
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


# ── Copy-paste fraud ──────────────────────────────────────────────────────────

def detect_copy_paste(
    nominations: list[dict],
    tenant_id: int,
    run_id: str,
    conn: pyodbc.Connection,
    similarity_threshold: float = 0.92,
    min_cluster_size: int = 3,
    chunk_size: int = 512,
    policy: dict | None = None,
) -> list[dict]:
    """
    Clusters of nominations whose description embeddings are mutually similar
    (cosine ≥ similarity_threshold). Uses sentence-transformers for embeddings
    and union-find for cluster formation.

    Embedding cache
    ---------------
    In-scope nomination text is immutable, so embeddings computed on a
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
    parameters = _pattern_config(policy, "CopyPaste").get("parameters", {})
    similarity_threshold = float(
        parameters.get("similarity_threshold", similarity_threshold)
    )
    min_cluster_size = int(
        parameters.get("minimum_cluster_size", min_cluster_size)
    )

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
            f"total nominated amount: ${total_amount:,})",
            total_amount=total_amount,
            policy=policy,
            signals={
                "similarity": min(max(
                    (avg_sim - similarity_threshold) /
                    max(1.0 - similarity_threshold, 0.001), 0.0
                ), 1.0),
                "cluster_size": min(
                    len(members) / max(float(
                        parameters.get("cluster_size_reference", 8)
                    ), 1.0), 1.0
                ),
                "exposure": min(
                    total_amount / max(float(
                        parameters.get("amount_reference", 10_000)
                    ), 1.0), 1.0
                ),
            },
        ))

    logger.info("  CopyPaste: %d clusters detected", len(findings))
    return findings


# ── Hidden candidate ──────────────────────────────────────────────────────────

def detect_hidden_candidate(
    nominations: list[dict],
    users: list[dict],
    tenant_id: int,
    run_id: str,
    min_text_mentions: int = 5,
    policy: dict | None = None,
) -> list[dict]:
    """
    Users whose full name appears frequently in nomination description text
    but who never appear as a BeneficiaryId — suggesting they are being
    benefited informally without being formally nominated.

    Only active users (those who appear at least once as NominatorId or
    BeneficiaryId) are considered as candidates, to avoid matching
    ex-employees mentioned in historical text.
    """
    parameters = _pattern_config(policy, "HiddenCandidate").get("parameters", {})
    min_text_mentions = int(parameters.get("minimum_mentions", min_text_mentions))
    mention_reference = max(float(parameters.get("mention_reference", 15)), 1.0)
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
                policy=policy,
                signals={"mention": min(count / mention_reference, 1.0)},
            ))

    logger.info("  HiddenCandidate: %d detected", len(findings))
    return findings


# ── Complete Graph snapshot ──────────────────────────────────────────────────

def _populate_graph_flag_snapshots(
    conn: pyodbc.Connection,
    tenant_id: int,
    findings: list[dict],
    as_of_date: str,
    run_id: str,
) -> None:
    """Materialise one complete, evidence-rich Graph snapshot.

    IntegrityComponentStatus is updated after this transaction and records
    every successful run, including a clean run with zero findings.
    UserGraphFlags contains only affected nominators and beneficiaries.
    """
    cur = conn.cursor()

    _SEVERITY_RANK = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
    active_findings = [
        finding for finding in findings
        if finding.get("PatternType") != "ApproverAffinity"
    ]

    user_flags: dict[int, dict] = defaultdict(lambda: {
        "IsInRing":                 0,
        "RingMaxUserCount":         0,
        "RingMaxNominationCount":   0,
        "IsSuperNominator":         0,
        "IsInCopyPasteCluster":     0,
        "CopyPasteClusterSize":     0,
        "HighestSeverity":          None,
        "Findings":                 [],
        "_severity_rank":           0,
    })

    def _update_severity(flags: dict, severity: str) -> None:
        rank = _SEVERITY_RANK.get(severity, 0)
        if rank > flags["_severity_rank"]:
            flags["_severity_rank"] = rank
            flags["HighestSeverity"] = severity

    for f in active_findings:
        ptype    = f["PatternType"]
        severity = f["Severity"]
        users    = json.loads(f["AffectedUsers"])
        nom_ids  = json.loads(f.get("NominationIds") or "[]")

        evidence = {
            "finding_hash": f.get("FindingHash"),
            "pattern_type": ptype,
            "severity": severity,
            "nomination_ids": nom_ids,
            "detail": f.get("Detail"),
            "total_amount": f.get("TotalAmount", 0),
            "finding_score": float(f.get("FindingScore") or 0),
            "scoring_policy_version": f.get("ScoringPolicyVersion"),
            "score_components": json.loads(
                f.get("ScoreComponentsJson") or "{}"
            ),
            "enabled_for_routing": bool(f.get("EnabledForRouting", True)),
            "applicable_roles": list(f.get(
                "ApplicableRoles", ["nominator", "beneficiary"]
            )),
        }

        for uid in users:
            uf = user_flags[uid]
            _update_severity(uf, severity)
            uf["Findings"].append(evidence)

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
    # A same-day rerun is a full replacement, not a partial merge.
    cur.execute(
        "DELETE FROM dbo.UserGraphFlags WHERE TenantId = ? AND AsOfDate = ?",
        (tenant_id, as_of_date),
    )

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
                uf["HighestSeverity"],
                json.dumps(uf["Findings"], separators=(",", ":")),
            )
            for uid, uf in user_flags.items()
        ]

        cur.executemany("""
            INSERT INTO dbo.UserGraphFlags
                (TenantId, UserId, AsOfDate,
                 IsInRing, RingMaxUserCount, RingMaxNominationCount,
                 IsSuperNominator,
                 IsInCopyPasteCluster, CopyPasteClusterSize,
                 HighestSeverity, FindingsJson)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows_ugf)

        logger.info(
            "  UserGraphFlags: inserted %d affected-user row(s) for AsOfDate=%s",
            len(rows_ugf), as_of_date,
        )

    logger.info(
        "  Complete Graph snapshot staged for AsOfDate=%s (%d finding(s)); "
        "component status will commit it atomically",
        as_of_date, len(active_findings),
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def _process_tenant(
    conn,
    tenant_id: int,
    findings_table: str,
    default_window_days: int,
    ring_max_cluster: int,
    run_id: str,
) -> int:
    """Detect and persist one tenant's graph snapshot and component status."""
    logger.info("Tenant %d", tenant_id)

    policy = _load_active_graph_policy(conn, tenant_id, default_window_days)
    window_days = int(policy["detection_window_days"])
    logger.info(
        "  Graph policy: v%d, %s; detection window: %d days",
        policy["version"], policy["strategy"], window_days,
    )

    nominations = _load_nominations(conn, tenant_id, window_days)
    users = _load_users(conn, tenant_id)
    ever_active_ids = _load_ever_active_user_ids(conn, tenant_id)
    logger.info(
        "  Nominations (last %d days): %d  |  Users: %d  |  Ever-active: %d",
        window_days, len(nominations), len(users), len(ever_active_ids),
    )
    if not nominations:
        logger.info(
            "  No Pending/Approved/Paid nominations in window — recording a complete "
            "snapshot after non-nomination detectors run."
        )

    existing_hashes = _load_existing_hashes(conn, tenant_id, findings_table)
    logger.info("  Existing hashes in table: %d", len(existing_hashes))

    detected_findings: list[dict] = []

    def enabled(pattern_type: str) -> bool:
        return bool(_pattern_config(policy, pattern_type).get("enabled", False))

    if enabled("Ring"):
        detected_findings.extend(detect_rings(
            nominations, users, tenant_id, run_id, ring_max_cluster, policy
        ))
    if enabled("BipartiteDenseBlock"):
        detected_findings.extend(detect_bipartite_dense_blocks(
            nominations, tenant_id, run_id, policy
        ))
    if enabled("TemporalBurst"):
        detected_findings.extend(detect_temporal_bursts(
            nominations, tenant_id, run_id, policy
        ))
    if enabled("SuperNominator"):
        detected_findings.extend(detect_super_nominators(
            nominations, tenant_id, run_id, policy
        ))
    if enabled("SuperBeneficiary"):
        detected_findings.extend(detect_super_beneficiaries(
            nominations, tenant_id, run_id, policy
        ))
    if enabled("CopyPaste"):
        detected_findings.extend(detect_copy_paste(
            nominations, tenant_id, run_id, conn, policy=policy
        ))
    if enabled("HiddenCandidate"):
        detected_findings.extend(detect_hidden_candidate(
            nominations, users, tenant_id, run_id, policy=policy
        ))
    if enabled("Desert"):
        detected_findings.extend(detect_deserts(
            ever_active_ids, users, tenant_id, run_id, policy
        ))

    _save_findings(conn, detected_findings, findings_table, existing_hashes)
    logger.info("  Tenant %d total findings: %d", tenant_id, len(detected_findings))

    as_of_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _populate_graph_flag_snapshots(
        conn, tenant_id, detected_findings, as_of_date, run_id
    )
    upsert_component_status(
        conn, tenant_id=tenant_id, component="GRAPH", attempt_status="SUCCEEDED",
        serving_status="AVAILABLE",
        serving_version=f"graph-policy-v{policy['version']}",
        serving_as_of=as_of_date,
        diagnostics={
            "nomination_count": len(nominations), "user_count": len(users),
            "finding_count": len(detected_findings), "window_days": window_days,
            "scoring_policy_version": policy["version"],
            "scoring_strategy": policy["strategy"],
            "snapshot_max_age_days": policy["snapshot_max_age_days"],
        },
        run_id=run_id,
    )

    finding_count = len(detected_findings)
    del nominations, users, detected_findings
    gc.collect()
    logger.info("  Tenant %d memory freed.", tenant_id)
    return finding_count


def main(tenants_to_process: list | None = None) -> None:
    log_level = os.getenv("LOGGING_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logger.info("graph_analytics — starting")

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

    # Evict only embeddings older than every active tenant policy requires.
    embedding_window_days = _maximum_active_detection_window(
        conn, default_window_days
    )
    _evict_stale_embeddings(conn, embedding_window_days)

    tenants = _load_tenants(conn)
    if tenants_to_process is not None:
        tenants = [t for t in tenants if t in tenants_to_process]
        if not tenants:
            logger.warning("Tenant(s) %s not found in database. Exiting.", tenants_to_process)
            conn.close()
            return
    logger.info("Tenants to process: %s", tenants)

    total_findings = 0
    failed: list[int] = []

    for tenant_id in tenants:
        try:
            total_findings += _process_tenant(
                conn, tenant_id, findings_table, default_window_days,
                ring_max_cluster, run_id,
            )
        except Exception as exc:
            logger.error("Tenant %d graph analytics failed: %s", tenant_id, exc, exc_info=True)
            failed.append(tenant_id)
            try:
                conn.rollback()
                upsert_component_status(
                    conn, tenant_id=tenant_id, component="GRAPH",
                    attempt_status="FAILED", reason_code="ANALYTICS_FAILED",
                    reason_detail=str(exc), run_id=run_id,
                )
            except Exception:
                logger.error(
                    "Tenant %d Graph failure status could not be persisted",
                    tenant_id, exc_info=True,
                )

    conn.close()
    logger.info(
        "graph_analytics — done. RunId=%s  Total findings=%d",
        run_id, total_findings,
    )
    if failed:
        raise RuntimeError(f"Graph analytics failed for tenant(s): {failed}")


if __name__ == "__main__":
    main()
