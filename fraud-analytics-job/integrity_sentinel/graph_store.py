"""Integrity Sentinel persistence used by Graph analytics stages."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

import numpy as np

from integrity_engine.graph.history_windows import detector_windows


logger = logging.getLogger(__name__)

GRAPH_FINDINGS_TABLE = "integrity.GraphPatternFindings"


def load_active_graph_policy(
    connection: Any,
    tenant_id: int,
    default_window_days: int,
    tenant_integrity_config: dict | None = None,
) -> dict:
    """Load the active Sentinel policy and apply source-owned window settings."""

    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT TOP 1 p.PolicyId, p.PolicyVersion, p.ScoringStrategy,
               p.LowThreshold, p.MediumThreshold, p.HighThreshold, p.CriticalThreshold,
               p.DetectionWindowDays, p.SnapshotMaxAgeDays
        FROM integrity.GraphScoringPolicies p
        WHERE p.TenantId = ? AND p.Status = 'ACTIVE'
        ORDER BY p.PolicyVersion DESC
        """,
        tenant_id,
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError(
            f"Tenant {tenant_id} has no active Graph Analytics scoring policy"
        )

    tenant_integrity_config = tenant_integrity_config or {}
    graph_config = tenant_integrity_config.get("graph_pattern")
    if not isinstance(graph_config, dict):
        graph_config = {}
    configured_window = graph_config.get("detection_window_days")
    window_days = (
        configured_window
        if isinstance(configured_window, int)
        else int(row[7] or default_window_days)
    )
    if window_days <= 0:
        raise ValueError(f"Tenant {tenant_id} Graph detection window must be positive")

    policy = {
        "policy_id": int(row[0]),
        "version": int(row[1]),
        "strategy": str(row[2]),
        "thresholds": {
            "low": float(row[3]),
            "medium": float(row[4]),
            "high": float(row[5]),
            "critical": float(row[6]),
        },
        "detection_window_days": window_days,
        "snapshot_max_age_days": int(row[8] or 14),
        "patterns": {},
    }
    raw_detector_windows = graph_config.get("detector_windows")
    policy["detector_windows"] = (
        raw_detector_windows if isinstance(raw_detector_windows, dict) else {}
    )
    detector_windows(policy)

    cursor.execute(
        """
        SELECT PatternType, Enabled, EnabledForRouting, ApplicableRolesJson,
               BaseScore, MinimumScore, MaximumScore, ParametersJson,
               CandidateEvaluationJson
        FROM integrity.GraphScoringPatternParameters
        WHERE PolicyId = ?
        """,
        policy["policy_id"],
    )
    for item in cursor.fetchall():
        try:
            roles = json.loads(item[3]) if item[3] else []
            parameters = json.loads(item[7]) if item[7] else {}
            candidate_evaluation = json.loads(item[8]) if item[8] else {}
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
            "candidate_evaluation": candidate_evaluation,
        }
    return policy


def save_findings(connection: Any, findings: list[dict], table: str) -> None:
    """Refresh unique findings within the caller's publication transaction."""

    if not findings:
        return
    seen_this_run: set[str] = set()
    unique_findings: list[dict] = []
    for finding in findings:
        finding_hash = finding["FindingHash"]
        if finding_hash not in seen_this_run:
            seen_this_run.add(finding_hash)
            unique_findings.append(finding)
    logger.info(
        "  Complete snapshot: %d candidate(s), %d unique, %d duplicates skipped.",
        len(findings),
        len(unique_findings),
        len(findings) - len(unique_findings),
    )
    if not unique_findings:
        return

    sql = f"""
        MERGE {table} WITH (HOLDLOCK) AS target
        USING (SELECT ? AS TenantId, ? AS PatternType, ? AS Severity,
                      ? AS AffectedUsers, ? AS NominationIds, ? AS Detail,
                      ? AS DetectedAt, ? AS RunId, ? AS FindingHash,
                      ? AS TotalAmount, ? AS FindingScore, ? AS ScoringPolicyVersion,
                      ? AS ScoreComponentsJson) AS src
        ON target.TenantId=src.TenantId AND target.FindingHash=src.FindingHash
        WHEN MATCHED THEN UPDATE SET
            Severity=src.Severity, Detail=src.Detail, DetectedAt=src.DetectedAt,
            RunId=src.RunId, TotalAmount=src.TotalAmount, FindingScore=src.FindingScore,
            ScoringPolicyVersion=src.ScoringPolicyVersion,
            ScoreComponentsJson=src.ScoreComponentsJson, SnapshotComplete=0
        WHEN NOT MATCHED THEN INSERT
            (TenantId, PatternType, Severity, AffectedUsers, NominationIds, Detail,
             DetectedAt, RunId, FindingHash, TotalAmount, FindingScore,
             ScoringPolicyVersion, ScoreComponentsJson, SnapshotComplete)
        VALUES (src.TenantId, src.PatternType, src.Severity, src.AffectedUsers,
                src.NominationIds, src.Detail, src.DetectedAt, src.RunId, src.FindingHash,
                src.TotalAmount, src.FindingScore, src.ScoringPolicyVersion,
                src.ScoreComponentsJson, 0);
    """
    rows = [
        (
            finding["TenantId"],
            finding["PatternType"],
            finding["Severity"],
            finding["AffectedUsers"],
            finding["NominationIds"],
            finding["Detail"],
            finding["DetectedAt"],
            finding["RunId"],
            finding["FindingHash"],
            finding.get("TotalAmount", 0),
            finding.get("FindingScore"),
            finding.get("ScoringPolicyVersion"),
            finding.get("ScoreComponentsJson"),
        )
        for finding in unique_findings
    ]
    connection.cursor().executemany(sql, rows)
    logger.info("  Refreshed %d unique finding(s) in %s.", len(rows), table)


def evict_stale_nomination_embeddings(connection: Any, window_days: int) -> None:
    cursor = connection.cursor()
    cursor.execute(
        """
        DELETE FROM integrity.NomGraph_NominationEmbedding
        WHERE EmbeddedAt < DATEADD(DAY, -?, SYSUTCDATETIME())
        """,
        window_days,
    )
    deleted = cursor.rowcount
    connection.commit()
    if deleted:
        logger.info("Evicted %d stale embedding(s) from cache.", deleted)


def load_cached_nomination_embeddings(
    connection: Any, nomination_ids: list[int]
) -> dict[int, np.ndarray]:
    if not nomination_ids:
        return {}
    result: dict[int, np.ndarray] = {}
    cursor = connection.cursor()
    for start in range(0, len(nomination_ids), 2_000):
        batch = nomination_ids[start : start + 2_000]
        placeholders = ",".join("?" * len(batch))
        cursor.execute(
            f"SELECT NominationId, Embedding "
            f"FROM integrity.NomGraph_NominationEmbedding "
            f"WHERE NominationId IN ({placeholders})",
            batch,
        )
        for row in cursor.fetchall():
            result[row[0]] = np.frombuffer(bytes(row[1]), dtype=np.float32).copy()
    return result


def save_nomination_embeddings(
    connection: Any, embeddings: dict[int, np.ndarray]
) -> None:
    if not embeddings:
        return
    rows = [
        (nomination_id, vector.astype(np.float32).tobytes(), nomination_id)
        for nomination_id, vector in embeddings.items()
    ]
    connection.cursor().executemany(
        """
        INSERT INTO integrity.NomGraph_NominationEmbedding
            (NominationId, Embedding, EmbeddedAt)
        SELECT ?, CAST(? AS VARBINARY(MAX)), GETUTCDATE()
        WHERE NOT EXISTS (
            SELECT 1 FROM integrity.NomGraph_NominationEmbedding
            WHERE NominationId = ?
        )
        """,
        rows,
    )
    connection.commit()
    logger.info("  Cached %d new embedding(s).", len(embeddings))


def populate_graph_flag_snapshot(
    connection: Any,
    tenant_id: int,
    findings: list[dict],
    as_of_date: str,
    run_id: str,
) -> None:
    cursor = connection.cursor()
    active_findings = [
        finding
        for finding in findings
        if finding.get("PatternType") != "ApproverAffinity"
    ]
    user_flags: dict[int, list] = defaultdict(list)
    for finding in active_findings:
        evidence = {
            "snapshot_run_id": run_id,
            "finding_hash": finding.get("FindingHash"),
            "pattern_type": finding["PatternType"],
            "severity": finding["Severity"],
            "nomination_ids": json.loads(finding.get("NominationIds") or "[]"),
            "detail": finding.get("Detail"),
            "total_amount": finding.get("TotalAmount", 0),
            "finding_score": float(finding.get("FindingScore") or 0),
            "scoring_policy_version": finding.get("ScoringPolicyVersion"),
            "score_components": json.loads(
                finding.get("ScoreComponentsJson") or "{}"
            ),
            "enabled_for_routing": bool(finding.get("EnabledForRouting", True)),
            "applicable_roles": list(
                finding.get("ApplicableRoles", ["nominator", "beneficiary"])
            ),
        }
        for user_id in json.loads(finding["AffectedUsers"]):
            user_flags[user_id].append(evidence)

    cursor.execute(
        "DELETE FROM integrity.UserGraphFlags WHERE TenantId = ? AND AsOfDate = ?",
        (tenant_id, as_of_date),
    )
    if user_flags:
        rows = [
            (
                tenant_id,
                user_id,
                as_of_date,
                json.dumps(flags, separators=(",", ":"), allow_nan=False),
            )
            for user_id, flags in user_flags.items()
        ]
        cursor.executemany(
            """
            INSERT INTO integrity.UserGraphFlags
                (TenantId, UserId, AsOfDate, FindingsJson)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        logger.info(
            "  UserGraphFlags: inserted %d affected-user row(s) for AsOfDate=%s",
            len(rows),
            as_of_date,
        )
    logger.info(
        "  Complete Graph snapshot staged for AsOfDate=%s (%d finding(s)); "
        "component status will commit it atomically",
        as_of_date,
        len(active_findings),
    )


def lock_graph_component(connection: Any, tenant_id: int) -> None:
    connection.cursor().execute(
        """
        SELECT TenantId FROM integrity.IntegrityComponentStatus WITH (UPDLOCK, HOLDLOCK)
        WHERE TenantId=? AND Component='GRAPH'
        """,
        (tenant_id,),
    ).fetchall()


def read_graph_serving_version(connection: Any, tenant_id: int) -> str | None:
    row = connection.cursor().execute(
        """
        SELECT ServingVersion
        FROM integrity.IntegrityComponentStatus
        WHERE TenantId=? AND Component='GRAPH'
        """,
        (tenant_id,),
    ).fetchone()
    return str(row[0]) if row and row[0] else None
