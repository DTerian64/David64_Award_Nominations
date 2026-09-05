"""
Database access for the integrity-check worker.

Focused subset of queries needed by inference/handler.py and its checks:

  Idempotency:
    claim_message()                 — insert into dbo.ProcessedEvents
    update_processed_event_result() — update result after handling

  Nomination data:
    get_nomination_details()        — full nomination for fraud feature engineering
    set_nomination_status()         — move to Pending / PendingHRBPReview

  Tenant config:
    get_tenant_desc_check_config()  — per-tenant description check thresholds
    get_tenant_integrity_config()   — per-tenant fraud pipeline config (windows, thresholds)

  Fraud history lookups (called by inference/random_forest_check.py):
    get_nominator_history()         — past nominations sent by a user
    get_beneficiary_history()       — past nominations received by a user
    get_approver_history()          — past approvals by a user
    check_reciprocal_nomination()   — has B ever nominated A?
    get_pair_nomination_count()     — how many times has A nominated B?
    get_beneficiary_descriptions()  — past descriptions written BY the beneficiary
    get_nominator_descriptions()    — past descriptions written BY the nominator

  Graph component lookups:
    get_graph_component_snapshot()  — latest complete snapshot for independent graph scoring

  Decision persistence:
    save_integrity_decision_results() — persist the canonical four-engine decision

  GNN model support (called by gnn_check.py):
    get_gnn_user_embeddings()       — version-matched node embeddings for a user set
"""

import json
import math
import logging
import os
import struct
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pyodbc

from .azure_credential import credential

logger = logging.getLogger("integrity_check.db")

# created_by / updated_by marker for this service's autonomous writes (no human actor).
_AUDIT_ACTOR = "svc:integrity-check"
_PENDING_CLAIM_TIMEOUT_SECONDS = int(
    os.getenv("IDEMPOTENCY_PENDING_TIMEOUT_SECONDS", "120")
)


class MessageClaimInProgress(RuntimeError):
    """A prior worker still owns a non-stale idempotency claim."""

# ── Connection (Entra token via Managed Identity) ─────────────────────────────
# DefaultAzureCredential resolves the container's user-assigned MI (selected by
# MI_CLIENT_ID) in Azure, or the developer's az / VS Code login locally.
# No SQL username/password.
_SERVER   = os.environ["SQL_SERVER"]
_DATABASE = os.environ["SQL_DATABASE"]
_DRIVER   = os.getenv("DB_DRIVER", "{ODBC Driver 18 for SQL Server}")

_SQL_COPT_SS_ACCESS_TOKEN = 1256
_AZURE_SQL_SCOPE          = "https://database.windows.net/.default"
_BASE_CONNECTION_STRING = (
    f"Driver={_DRIVER};"
    f"Server={_SERVER};"
    f"Database={_DATABASE};"
    f"Encrypt=yes;"
    f"TrustServerCertificate=no;"
)


@contextmanager
def _get_conn():
    token        = credential.get_token(_AZURE_SQL_SCOPE).token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
    conn = pyodbc.connect(
        _BASE_CONNECTION_STRING,
        attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct},
    )
    try:
        yield conn
    finally:
        conn.close()


# ── Per-tenant description check config ──────────────────────────────────────

@dataclass
class DescCheckConfig:
    """
    Per-tenant thresholds for description quality checks.

    Populated from dbo.Tenants.desc_check_config (NVARCHAR(MAX) JSON).
    NULL column → all fields take their defaults (English, word-count based).

    Fields
    ------
    embed_model
        Sentence-transformer model name used for semantic similarity.
        'all-MiniLM-L6-v2'                       — English-optimised (default)
        'paraphrase-multilingual-MiniLM-L12-v2'  — multilingual (CJK, etc.)

    use_char_count
        When True, length gate uses character count (appropriate for CJK
        languages where one character carries full-word meaning).

    min_char_count / min_word_count
        Minimum length thresholds — enforced at the API Pydantic layer, not
        the pipeline, but stored here so the API can read the same config.

    category_alignment_threshold
        Minimum cosine similarity between description and category embeddings.
        Descriptions scoring below this are auto-rejected (Check A).
        0.0 disables the check — correct for tenants with no categories.

    duplicate_similarity_threshold
        Cosine similarity above which a description is considered a near-
        duplicate of the nominator's own prior descriptions.  Triggers a
        warning flag routed to HRBP review (Check B), not an auto-reject.

    boilerplate_phrases
        Lowercased phrases that trigger an API-layer 422.  Language-specific.
    """
    embed_model:                    str       = "all-MiniLM-L6-v2"
    use_char_count:                 bool      = False
    min_char_count:                 int       = 12
    min_word_count:                 int       = 3
    category_alignment_threshold:   float     = 0.15
    duplicate_similarity_threshold: float     = 0.85
    boilerplate_phrases:            List[str] = field(default_factory=list)

    # ── Check A: LLM semantic evidence ───────────────────────────────────────
    # llm_category_check_enabled
    #     Master switch — when False, Check A uses embedding evidence only.
    #
    # llm_fit_threshold
    #     Nominations whose LLM category_fit_score falls below this value are
    #     flagged for HRBP review.  Does not cause an auto-reject on its own
    #     (only is_coherent=false does that).
    #
    # llm_instructions
    #     Free-text addendum injected into the LLM prompt after the base
    #     evaluation criteria.  Lets each tenant override default behaviour —
    #     e.g. "do not penalise Korean-language descriptions" or "ignore
    #     low_specificity flags for awards under 200".
    llm_category_check_enabled:     bool      = False
    llm_fit_threshold:              float     = 0.40
    llm_instructions:               Optional[str] = None


def get_tenant_desc_check_config(tenant_id: int) -> DescCheckConfig:
    """
    Load desc_check_config JSON from dbo.Tenants and return a DescCheckConfig.

    Missing keys fall back to dataclass defaults, so partial configs are safe.
    A NULL column (or any parse error) returns a fully-defaulted config.
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT desc_check_config FROM dbo.Tenants WHERE TenantId = ?",
            (tenant_id,),
        )
        row = cursor.fetchone()

    raw = row[0] if row else None
    if not raw:
        return DescCheckConfig()

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "Invalid JSON in desc_check_config for tenant %d — using defaults",
            tenant_id,
        )
        return DescCheckConfig()

    return DescCheckConfig(
        embed_model=data.get("embed_model", DescCheckConfig.embed_model),
        use_char_count=bool(data.get("use_char_count", DescCheckConfig.use_char_count)),
        min_char_count=int(data.get("min_char_count", DescCheckConfig.min_char_count)),
        min_word_count=int(data.get("min_word_count", DescCheckConfig.min_word_count)),
        category_alignment_threshold=float(
            data.get("category_alignment_threshold",
                     DescCheckConfig.category_alignment_threshold)
        ),
        duplicate_similarity_threshold=float(
            data.get("duplicate_similarity_threshold",
                     DescCheckConfig.duplicate_similarity_threshold)
        ),
        boilerplate_phrases=[
            p.lower() for p in data.get("boilerplate_phrases", [])
        ],
        llm_category_check_enabled=bool(
            data.get("llm_category_check_enabled", False)
        ),
        llm_fit_threshold=float(
            data.get("llm_fit_threshold", DescCheckConfig.llm_fit_threshold)
        ),
        llm_instructions=data.get("llm_instructions") or None,
    )


# ── Per-tenant integrity config ───────────────────────────────────────────────

def get_tenant_integrity_config(tenant_id: int) -> dict:
    """
    Load and parse integrity_config JSON from dbo.Tenants for this tenant.

    Returns the parsed dict, or {} if the column is NULL or absent.
    Callers use .get() with explicit defaults so partial configs are safe.

    Schema (all keys optional — missing keys fall back to system defaults):
    {
      "graph_pattern": { "detection_window_days": 365 },
      "score_routing": {
          "critical_threshold": 80,
          "high_threshold":     60,
          "medium_threshold":   40,
          "low_threshold":      20
      },
      "gnn": {
          "score_routing": {
              "critical_threshold": 85,
              "high_threshold":     65,
              "medium_threshold":   45,
              "low_threshold":      25
          }
      },
    }

    The cache lifetime is the container process lifetime — config changes
    require a container restart (acceptable operational behaviour).
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT integrity_config FROM dbo.Tenants WHERE TenantId = ?",
            (tenant_id,),
        )
        row = cursor.fetchone()

    raw = row[0] if row else None
    if not raw:
        return {}

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "Invalid JSON in integrity_config for tenant %d — using defaults",
            tenant_id,
        )
        return {}


# ── Producer-owned component availability ────────────────────────────────────

def get_integrity_component_statuses(tenant_id: int) -> dict[str, dict]:
    """Return current RF, Graph, and GNN producer status for one tenant."""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT Component, ServingStatus, ServingVersion, ServingAsOf,
                   LastAttemptStatus, ReasonCode, ReasonDetail, DiagnosticsJson,
                   LastAttemptAt, LastSuccessfulAt, RunId, UpdatedAt
            FROM dbo.IntegrityComponentStatus
            WHERE TenantId = ?
        """, tenant_id)
        rows = cursor.fetchall()

    return {
        str(row[0]).upper(): {
            "component": str(row[0]).upper(),
            "serving_status": row[1],
            "serving_version": row[2],
            "serving_as_of": row[3],
            "last_attempt_status": row[4],
            "reason_code": row[5],
            "reason_detail": row[6],
            "diagnostics_json": row[7],
            "last_attempt_at": row[8],
            "last_successful_at": row[9],
            "run_id": row[10],
            "updated_at": row[11],
        }
        for row in rows
    }


# ── Nomination details ────────────────────────────────────────────────────────

def get_nomination_details(nomination_id: int) -> Optional[dict]:
    """Fetch all nomination data needed for fraud feature engineering and routing."""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                n.NominationId,
                n.Amount,
                n.Currency,
                n.NominationDescription,
                n.Status,
                n.ApproverId,
                nominator.UserId          AS NominatorId,
                nominator.FirstName + ' ' + nominator.LastName AS NominatorName,
                nominator.userEmail       AS NominatorEmail,
                beneficiary.UserId        AS BeneficiaryId,
                beneficiary.FirstName + ' ' + beneficiary.LastName AS BeneficiaryName,
                beneficiary.userEmail     AS BeneficiaryEmail,
                approver.FirstName + ' ' + approver.LastName AS ApproverName,
                approver.userEmail        AS ApproverEmail,
                nc.category_description   AS CategoryDescription,
                nominator.TenantId        AS TenantId,
                n.CategoryId,
                n.NominationDate
            FROM  dbo.Nominations n
            INNER JOIN dbo.Users nominator   ON n.NominatorId   = nominator.UserId
            INNER JOIN dbo.Users beneficiary ON n.BeneficiaryId = beneficiary.UserId
            INNER JOIN dbo.Users approver    ON n.ApproverId    = approver.UserId
            LEFT  JOIN dbo.nomination_categories nc ON nc.id    = n.CategoryId
            WHERE n.NominationId = ?
        """, (nomination_id,))
        row = cursor.fetchone()

    if not row:
        return None

    return {
        "nomination_id":        int(row[0]),
        "amount":               float(row[1]),
        "currency":             row[2],
        "description":          row[3],
        "status":               row[4],
        "approver_id":          int(row[5]),
        "nominator_id":         int(row[6]),
        "nominator_name":       row[7],
        "nominator_email":      row[8],
        "beneficiary_id":       int(row[9]),
        "beneficiary_name":     row[10],
        "beneficiary_email":    row[11],
        "approver_name":        row[12],
        "approver_email":       row[13],
        "category_description": row[14],
        "tenant_id":            int(row[15]),
        "category_id":          row[16],
        "nomination_date":      row[17],
    }


def set_nomination_status(nomination_id: int, new_status: str) -> None:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE dbo.Nominations SET Status = ?, "
            "updated_at = SYSUTCDATETIME(), updated_by = ? WHERE NominationId = ?",
            (new_status, _AUDIT_ACTOR, nomination_id),
        )
        conn.commit()
        logger.info("Status updated",
                    extra={"nomination_id": nomination_id, "new_status": new_status})


def reject_nomination(nomination_id: int, reason: str, actor: str) -> None:
    """
    Reject a nomination and persist the rejection reason and actor.

    Separate from set_nomination_status() because that function is also used
    for non-rejection transitions (Pending, PendingHRBPReview).

    Args:
        nomination_id: The nomination to reject.
        reason:        Human-readable explanation surfaced to the nominator.
        actor:         one of handler.ACTOR_DESCRIPTION_CHECK (Check A quality
                       gate) or handler.ACTOR_FRAUD_ML (CRITICAL ML auto-reject).
                       These two must stay distinct: load_data() in
                       fraud-analytics-job excludes the former from training and
                       keeps the latter. Rendered verbatim to the nominator as
                       "Rejected by: {actor}", so keep it human-readable.
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE dbo.Nominations
            SET Status          = 'Rejected',
                RejectionReason = ?,
                RejectionActor  = ?,
                updated_at      = SYSUTCDATETIME(),
                updated_by      = ?
            WHERE NominationId  = ?
            """,
            (reason.strip() or None, actor, _AUDIT_ACTOR, nomination_id),
        )
        conn.commit()
        logger.info(
            "Nomination rejected",
            extra={
                "nomination_id": nomination_id,
                "actor":         actor,
                "reason":        reason,
            },
        )


# ── Fraud history lookups ─────────────────────────────────────────────────────

def get_nominator_history(nominator_id: int) -> list[tuple]:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT NominationId, BeneficiaryId, Amount, NominationDate
            FROM   dbo.Nominations
            WHERE  NominatorId = ?
            ORDER  BY NominationDate DESC
        """, (nominator_id,))
        return cursor.fetchall()


def get_beneficiary_history(beneficiary_id: int) -> list[tuple]:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT NominationId, NominatorId, Amount, NominationDate
            FROM   dbo.Nominations
            WHERE  BeneficiaryId = ?
            ORDER  BY NominationDate DESC
        """, (beneficiary_id,))
        return cursor.fetchall()


def get_approver_history(approver_id: int) -> list[tuple]:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT NominationId,
                   DATEDIFF(HOUR, NominationDate, ApprovedDate) AS HoursToApproval
            FROM   dbo.Nominations
            WHERE  ApproverId    = ?
              AND  ApprovedDate IS NOT NULL
            ORDER  BY NominationDate DESC
        """, (approver_id,))
        return cursor.fetchall()


def check_reciprocal_nomination(nominator_id: int, beneficiary_id: int) -> bool:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM dbo.Nominations
            WHERE NominatorId = ? AND BeneficiaryId = ?
        """, (beneficiary_id, nominator_id))
        row = cursor.fetchone()
        return (row[0] > 0) if row else False


def get_pair_nomination_count(nominator_id: int, beneficiary_id: int) -> int:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM dbo.Nominations
            WHERE NominatorId = ? AND BeneficiaryId = ?
        """, (nominator_id, beneficiary_id))
        row = cursor.fetchone()
        return row[0] if row else 0


def get_beneficiary_descriptions(beneficiary_id: int) -> list[str]:
    """Past descriptions written BY the beneficiary (as nominator) — capped at 20."""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TOP 20 NominationDescription
            FROM   dbo.Nominations
            WHERE  NominatorId            = ?
              AND  NominationDescription IS NOT NULL
              AND  NominationDescription  <> ''
            ORDER  BY NominationDate DESC
        """, (beneficiary_id,))
        return [row[0] for row in cursor.fetchall()]


def get_nominator_descriptions(
    nominator_id: int,
    exclude_nomination_id: Optional[int] = None,
) -> list[str]:
    """
    Past descriptions written BY this nominator — capped at 50.

    Used by description_check.py (Check B) to detect near-duplicate
    descriptions submitted by the same person across different nominations.

    exclude_nomination_id should always be the current nomination so it is
    not compared against itself (the nomination is already committed to the DB
    before the integrity-check event fires).
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        if exclude_nomination_id is not None:
            cursor.execute("""
                SELECT TOP 50 NominationDescription
                FROM   dbo.Nominations
                WHERE  NominatorId            = ?
                  AND  NominationId          <> ?
                  AND  NominationDescription IS NOT NULL
                  AND  NominationDescription  <> ''
                ORDER  BY NominationDate DESC
            """, (nominator_id, exclude_nomination_id))
        else:
            cursor.execute("""
                SELECT TOP 50 NominationDescription
                FROM   dbo.Nominations
                WHERE  NominatorId            = ?
                  AND  NominationDescription IS NOT NULL
                  AND  NominationDescription  <> ''
                ORDER  BY NominationDate DESC
            """, (nominator_id,))
        return [row[0] for row in cursor.fetchall()]


# ── Graph component lookups ──────────────────────────────────────────────────

class InvalidGraphSnapshot(ValueError):
    """A published Graph snapshot has missing or inconsistent evidence."""


def _parse_graph_findings(raw, policy_version, run_id):
    try:
        findings = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidGraphSnapshot("Missing or malformed FindingsJson") from exc
    if not isinstance(findings, list) or not findings:
        raise InvalidGraphSnapshot("An affected-user snapshot must contain a non-empty findings array")
    for finding in findings:
        if not isinstance(finding, dict):
            raise InvalidGraphSnapshot("Finding evidence must be an object")
        score = finding.get("finding_score")
        roles = finding.get("applicable_roles")
        if (
            not isinstance(score, (int, float)) or isinstance(score, bool)
            or not math.isfinite(score) or not 0 <= score <= 100
            or not isinstance(finding.get("enabled_for_routing"), bool)
            or not isinstance(roles, list) or not roles
            or any(role not in ("nominator", "beneficiary") for role in roles)
            or not isinstance(finding.get("finding_hash"), str) or not finding["finding_hash"]
            or not isinstance(finding.get("pattern_type"), str) or not finding["pattern_type"]
            or not isinstance(finding.get("nomination_ids"), list)
            or not isinstance(finding.get("score_components"), dict)
            or finding.get("scoring_policy_version") != policy_version
            or str(finding.get("snapshot_run_id")) != str(run_id)
        ):
            raise InvalidGraphSnapshot("Incomplete, invalid, or mismatched Graph finding evidence")
    return findings

def get_graph_component_snapshot(
    tenant_id: int,
    user_ids: list[int],
    component_status: Optional[dict] = None,
) -> Optional[dict]:
    """Return the latest completed graph snapshot for the requested users.

    IntegrityComponentStatus is the authoritative completed-run marker. A
    missing user row on that date means the user had no finding in that run; it does not
    fall back to an older finding.  Falling back would keep a resolved graph
    pattern alive forever and would make the live graph opinion incorrect.

    ``None`` means the tenant has never produced a graph snapshot, which callers
    must treat as "no opinion" rather than a clean score.
    """
    unique_ids = list(dict.fromkeys(int(uid) for uid in user_ids if uid is not None))

    status = component_status
    if status is None:
        status = get_integrity_component_statuses(tenant_id).get("GRAPH")
    if not status or str(status.get("serving_status") or "").upper() != "AVAILABLE":
        return None
    serving_as_of = status.get("serving_as_of")
    if not serving_as_of:
        return None
    with _get_conn() as conn:
        cursor = conn.cursor()
        # Keep the completed-run marker and user rows consistent across same-day
        # replacements. The read transaction holds this lock until connection close.
        cursor.execute("""
            SELECT ServingStatus, ServingAsOf, RunId, DiagnosticsJson
            FROM dbo.IntegrityComponentStatus WITH (HOLDLOCK)
            WHERE TenantId=? AND Component='GRAPH'
        """, (tenant_id,))
        marker = cursor.fetchone()
        if not marker or marker[0] != 'AVAILABLE' or not marker[1]:
            return None
        as_of = marker[1].date() if hasattr(marker[1], 'date') else marker[1]
        run_id = str(marker[2]) if marker[2] else None
        try:
            diagnostics = json.loads(marker[3])
        except (TypeError, ValueError) as exc:
            raise InvalidGraphSnapshot("Missing Graph snapshot metadata") from exc
        if not isinstance(diagnostics, dict) or diagnostics.get('snapshot_schema_version') != 2:
            raise InvalidGraphSnapshot("Graph snapshot refresh required")
        if not run_id or not isinstance(diagnostics.get('scoring_policy_version'), int):
            raise InvalidGraphSnapshot("Graph snapshot run/policy is missing")
        users: dict[int, dict] = {}
        if unique_ids:
            placeholders = ", ".join("?" for _ in unique_ids)
            cursor.execute(f"""
                SELECT UserId, FindingsJson
                FROM dbo.UserGraphFlags
                WHERE TenantId = ?
                  AND AsOfDate = ?
                  AND UserId IN ({placeholders})
            """, [tenant_id, as_of, *unique_ids])
            for found in cursor.fetchall():
                users[int(found[0])] = {
                    "findings": _parse_graph_findings(found[1], diagnostics['scoring_policy_version'], run_id),
                }

    return {
        "snapshot_as_of": as_of,
        "snapshot_run_id": run_id,
        "snapshot_finding_count": int(diagnostics.get("finding_count", 0)),
        "scoring_policy_version": diagnostics.get("scoring_policy_version"),
        "inference_snapshot_blob": diagnostics.get("inference_snapshot_blob"),
        "inference_snapshot_sha256": diagnostics.get("inference_snapshot_sha256"),
        "inference_snapshot_schema_version": diagnostics.get(
            "inference_snapshot_schema_version"
        ),
        "inference_snapshot_size_bytes": diagnostics.get(
            "inference_snapshot_size_bytes"
        ),
        "inference_snapshot_generated_at": diagnostics.get(
            "inference_snapshot_generated_at"
        ),
        "users": users,
    }


def get_graph_scoring_policy(
    tenant_id: int,
    policy_version: Optional[int] = None,
) -> Optional[dict]:
    """Return one tenant policy used to interpret a Graph snapshot."""
    with _get_conn() as conn:
        cursor = conn.cursor()
        if policy_version is None:
            cursor.execute("""
                SELECT TOP 1 PolicyId, PolicyVersion, Status, ScoringStrategy,
                       LowThreshold, MediumThreshold, HighThreshold, CriticalThreshold,
                       DetectionWindowDays, SnapshotMaxAgeDays
                FROM dbo.GraphScoringPolicies
                WHERE TenantId = ? AND Status = 'ACTIVE'
                ORDER BY PolicyVersion DESC
            """, tenant_id)
        else:
            cursor.execute("""
                SELECT TOP 1 PolicyId, PolicyVersion, Status, ScoringStrategy,
                       LowThreshold, MediumThreshold, HighThreshold, CriticalThreshold,
                       DetectionWindowDays, SnapshotMaxAgeDays
                FROM dbo.GraphScoringPolicies
                WHERE TenantId = ? AND PolicyVersion = ?
            """, tenant_id, policy_version)
        row = cursor.fetchone()
    if not row:
        return None
    return {
        "policy_id": int(row[0]),
        "policy_version": int(row[1]),
        "status": row[2],
        "scoring_strategy": row[3],
        "thresholds": {
            "low": float(row[4]), "medium": float(row[5]),
            "high": float(row[6]), "critical": float(row[7]),
        },
        "detection_window_days": int(row[8]),
        "snapshot_max_age_days": int(row[9]),
    }


# ── Canonical decision persistence ────────────────────────────────────────────

def save_integrity_decision_results(
    nomination_id: int,
    message_id: str,
    policy_version: str,
    decision: dict,
    engine_results: dict[str, dict],
    final_route: str,
    routing_rule: str,
    review_scope: Optional[str],
    decisive_engines: list[str],
) -> None:
    """Persist a decision with the tenant resolved from its nomination owner.

    TenantId is convenience data, not a replacement for authorization checks.
    A retry cannot silently reassign an existing decision to another tenant.
    """
    composite_score = (
        decision.get("final_score") if decision.get("decision_available") else None
    )
    engine_json = {
        name: json.dumps(payload, default=str, separators=(",", ":"))
        for name, payload in engine_results.items()
    }
    decisive_json = json.dumps(decisive_engines, separators=(",", ":"))
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            MERGE dbo.IntegrityDecisionResults AS target
            USING (
                SELECT n.NominationId, u.TenantId, ? AS SourceMessageId
                FROM dbo.Nominations n
                JOIN dbo.Users u ON u.UserId = n.NominatorId
                WHERE n.NominationId = ? AND u.TenantId IS NOT NULL
            ) AS source
                ON target.NominationId = source.NominationId
            WHEN MATCHED AND target.TenantId = source.TenantId AND (
                target.SourceMessageId = source.SourceMessageId
                OR target.SourceMessageId IS NULL
            ) THEN UPDATE SET
                TenantId = source.TenantId,
                DecisionSchemaVersion = ?, PolicyVersion = ?, SourceMessageId = ?,
                RfResultJson = ?, GraphResultJson = ?, GnnResultJson = ?,
                SemanticResultJson = ?, CompositeScore = ?,
                CompositeRiskLevel = ?, DecisiveEnginesJson = ?,
                FinalRoute = ?, RoutingRule = ?, ReviewScope = ?,
                ScoredBy = ?, UpdatedAt = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN INSERT (
                TenantId, NominationId, DecisionSchemaVersion, PolicyVersion,
                SourceMessageId, RfResultJson, GraphResultJson, GnnResultJson,
                SemanticResultJson, CompositeScore, CompositeRiskLevel,
                DecisiveEnginesJson, FinalRoute, RoutingRule, ReviewScope,
                ScoredBy
            ) VALUES (source.TenantId, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            message_id, nomination_id,
            2, policy_version, message_id,
            engine_json["rf"], engine_json["graph"], engine_json["gnn"],
            engine_json["semantic"], composite_score, decision.get("risk_level"),
            decisive_json, final_route, routing_rule, review_scope, _AUDIT_ACTOR,
            nomination_id, 2, policy_version, message_id,
            engine_json["rf"], engine_json["graph"], engine_json["gnn"],
            engine_json["semantic"], composite_score, decision.get("risk_level"),
            decisive_json, final_route, routing_rule, review_scope, _AUDIT_ACTOR,
        ))

        if cursor.rowcount == 0:
            raise RuntimeError(
                "Integrity decision was not saved: nomination tenant is unresolved "
                "or an existing decision has a different tenant/source message: "
                f"nomination {nomination_id}"
            )
        conn.commit()


# ── Idempotency (dbo.ProcessedEvents) ────────────────────────────────────────

def claim_message(
    message_id:   str,
    event_type:   str,
    nomination_id: Optional[int],
    processed_at: datetime,
) -> bool:
    """
    Claim a message for processing and return True only after prior success.

    Failed claims are immediately reusable. A pending claim is reusable only
    after its lease timeout; until then ``MessageClaimInProgress`` prevents the
    Service Bus delivery from being completed as though work had succeeded.
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO dbo.ProcessedEvents
                    (MessageId, EventType, NominationId, ProcessedAt, Result)
                VALUES (?, ?, ?, ?, 'pending')
            """, (message_id, event_type, nomination_id, processed_at))
            conn.commit()
            return False   # new — proceed

        except pyodbc.IntegrityError:
            cursor.execute(
                "SELECT Result, ProcessedAt FROM dbo.ProcessedEvents WHERE MessageId = ?",
                (message_id,),
            )
            existing = cursor.fetchone()
            prior = str(existing[0]).lower() if existing else None
            prior_at = existing[1] if existing else None

            if prior == "success":
                return True

            stale_pending = False
            if prior == "pending" and prior_at is not None:
                prior_utc = (
                    prior_at.replace(tzinfo=timezone.utc)
                    if prior_at.tzinfo is None
                    else prior_at.astimezone(timezone.utc)
                )
                current_utc = (
                    processed_at.replace(tzinfo=timezone.utc)
                    if processed_at.tzinfo is None
                    else processed_at.astimezone(timezone.utc)
                )
                stale_pending = current_utc - prior_utc >= timedelta(
                    seconds=_PENDING_CLAIM_TIMEOUT_SECONDS
                )

            if prior == "error" or stale_pending:
                cursor.execute("""
                    UPDATE dbo.ProcessedEvents
                    SET EventType = ?, NominationId = ?, ProcessedAt = ?,
                        Result = 'pending'
                    WHERE MessageId = ? AND Result = ? AND ProcessedAt = ?
                """, (
                    event_type, nomination_id, processed_at,
                    message_id, prior, prior_at,
                ))
                if cursor.rowcount != 1:
                    raise MessageClaimInProgress(
                        f"Message {message_id} was reclaimed by another worker"
                    )
                conn.commit()
                logger.warning(
                    "Reclaimed failed or stale message",
                    extra={
                        "message_id": message_id,
                        "nomination_id": nomination_id,
                        "prior_result": prior,
                        "stale_pending": stale_pending,
                    },
                )
                return False

            raise MessageClaimInProgress(
                f"Message {message_id} has an active pending claim"
            )


def update_processed_event_result(
    message_id: str,
    result: str,
    error: Optional[str] = None,
) -> None:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE dbo.ProcessedEvents SET Result = ? WHERE MessageId = ?",
            (result, message_id),
        )
        conn.commit()
    if error:
        logger.error("ProcessedEvent recorded as error",
                     extra={"message_id": message_id, "error": error})


# ===========================================================================
# NOMINATION LOG PERSISTENCE (SOC 2) — dbo.Nomination_Logs
# ===========================================================================

_NOMLOG_SQL = (
    "INSERT INTO dbo.Nomination_Logs "
    "(nomination_id, tenant_id, log_time, level, service, logger, message, "
    " message_id, details, exception, created_by, updated_by) "
    "VALUES (?, COALESCE(?, (SELECT TOP 1 u.TenantId FROM dbo.Nominations n "
    "JOIN dbo.Users u ON u.UserId = n.NominatorId WHERE n.NominationId = ?)), "
    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def insert_nomination_logs(rows: list) -> None:
    """Bulk-insert nomination log rows (called by the background log handler).
    created_at/updated_at come from the DB default (SYSUTCDATETIME())."""
    if not rows:
        return
    params = [
        (r["nomination_id"], r["tenant_id"], r["nomination_id"], r["log_time"], r["level"], r["service"],
         r["logger"], r["message"], r["message_id"], r["details"], r["exception"],
         r["created_by"], r["updated_by"])
        for r in rows
    ]
    with _get_conn() as conn:
        cursor = conn.cursor()
        # Do not enable pyodbc fast_executemany here. With SQL Server's
        # NVARCHAR(MAX) details/exception columns, the ODBC driver can bind a
        # 255-character (510-byte UTF-16) buffer and reject larger JSON records
        # with HY000 "String data, right truncation". Nomination-log batches
        # are small, so standard executemany is the safer correctness tradeoff.
        cursor.executemany(_NOMLOG_SQL, params)
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# GNN model support — called by gnn_check.py
# ═══════════════════════════════════════════════════════════════════════════════

def get_gnn_user_embeddings(
    tenant_id: int,
    user_ids: list,
    model_version: Optional[str] = None,
) -> dict:
    """
    Return {UserId: (embedding, as_of_date, model_version)} for the requested users.

    Selects, per user, the NEWEST snapshot whose ModelVersion equals the caller's
    decoder version — not the newest snapshot overall.

    That distinction is what makes a decoder-only rollback work. dbo.GNN_UserEmbeddings
    is append-only within its retention window, so restoring a previous
    gnn_head_tenant_<N>.pt is sufficient on its own: this query then picks up that
    decoder's own generation of embeddings. Matching on "newest overall" instead
    would leave a rolled-back decoder permanently unable to score, because every
    lookup would return embeddings from a version it was not trained against.

    model_version=None returns the newest snapshot for each user regardless of
    version. That is NOT a scoring path — gnn_check.py uses it only to tell a
    genuine cold-start user (no embeddings at all) apart from a decoder whose
    generation of embeddings is missing (rollback gone wrong, or a weekly run
    that never published). Those need different responses, and conflating them
    hides the second behind the first.

    Users with no matching snapshot are simply absent from the result; the caller
    decides whether that is fatal (nominator/beneficiary) or tolerable (approver).

    Embedding bytes are float32 written by numpy.ndarray.tobytes() in the weekly
    job, and are read back with the same dtype. A dimension mismatch against the
    decoder is caught by the caller.
    """
    if not user_ids:
        return {}

    unique_ids = sorted(set(int(u) for u in user_ids if u is not None))
    if not unique_ids:
        return {}

    placeholders = ",".join("?" for _ in unique_ids)
    version_filter = "AND ModelVersion = ?" if model_version is not None else ""
    outer_filter   = "AND e.ModelVersion = ?" if model_version is not None else ""
    sql = f"""
        SELECT e.UserId, e.Embedding, e.AsOfDate, e.ModelVersion
        FROM   dbo.GNN_UserEmbeddings e
        JOIN  (
                 SELECT UserId, MAX(AsOfDate) AS AsOfDate
                 FROM   dbo.GNN_UserEmbeddings
                 WHERE  TenantId = ?
                   {version_filter}
                   AND  UserId IN ({placeholders})
                 GROUP BY UserId
              ) latest
          ON  latest.UserId   = e.UserId
         AND  latest.AsOfDate = e.AsOfDate
        WHERE e.TenantId = ?
          {outer_filter}
    """
    params = [tenant_id]
    if model_version is not None:
        params.append(model_version)
    params += unique_ids
    params.append(tenant_id)
    if model_version is not None:
        params.append(model_version)

    out: dict = {}
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        for user_id, blob, as_of, version in cursor.fetchall():
            import numpy as _np
            vec = _np.frombuffer(bytes(blob), dtype=_np.float32)
            out[int(user_id)] = (vec, as_of, version)

    missing = set(unique_ids) - set(out)
    if missing:
        logger.debug(
            "No GNN embedding (tenant=%d, version=%s) for user(s) %s",
            tenant_id, model_version, sorted(missing),
        )
    return out
