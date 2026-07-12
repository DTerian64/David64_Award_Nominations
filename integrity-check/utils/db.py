"""
Database access for the integrity-check worker.

Focused subset of queries needed by handler.py and fraud_check.py:

  Idempotency:
    claim_message()                 — insert into dbo.ProcessedEvents
    update_processed_event_result() — update result after handling

  Nomination data:
    get_nomination_details()        — full nomination for fraud feature engineering
    set_nomination_status()         — move to Pending / PendingHRBPReview

  Tenant config:
    get_tenant_desc_check_config()  — per-tenant description check thresholds
    get_tenant_integrity_config()   — per-tenant fraud pipeline config (windows, thresholds)

  Fraud history lookups (called by fraud_check.py):
    get_nominator_history()         — past nominations sent by a user
    get_beneficiary_history()       — past nominations received by a user
    get_approver_history()          — past approvals by a user
    check_reciprocal_nomination()   — has B ever nominated A?
    get_pair_nomination_count()     — how many times has A nominated B?
    get_beneficiary_descriptions()  — past descriptions written BY the beneficiary
    get_nominator_descriptions()    — past descriptions written BY the nominator

  Graph flag lookups (called by fraud_check.py):
    get_user_graph_flags()          — latest UserGraphFlags for nominator + beneficiary
    get_approver_graph_flags()      — latest UserGraphFlags + ApproverPairFlags for approver

  Fraud score persistence:
    save_p2p_fraud_score()          — upsert into dbo.P2P_FraudScores
    save_hrbp_fraud_flags()         — insert into dbo.HRBP_FraudFlags
"""

import json
import logging
import os
import struct
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import pyodbc

from .azure_credential import credential

logger = logging.getLogger("integrity_check.db")

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

    # ── Check C: LLM semantic evaluation ─────────────────────────────────────
    # llm_category_check_enabled
    #     Master switch — when False, Check C is skipped entirely.
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
      }
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
                n.CategoryId
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
    }


def set_nomination_status(nomination_id: int, new_status: str) -> None:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE dbo.Nominations SET Status = ? WHERE NominationId = ?",
            (new_status, nomination_id),
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
        actor:         "Fraud Detection" for auto-rejects from this service.
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE dbo.Nominations
            SET Status          = 'Rejected',
                RejectionReason = ?,
                RejectionActor  = ?
            WHERE NominationId  = ?
            """,
            (reason.strip() or None, actor, nomination_id),
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


# ── Graph flag lookups ───────────────────────────────────────────────────────

def get_user_graph_flags(
    tenant_id:     int,
    nominator_id:  int,
    beneficiary_id: int,
) -> dict:
    """
    Return the latest UserGraphFlags snapshot for the nominator and beneficiary.

    Queries the two rows separately (nominator + beneficiary) and returns a
    single dict of composite features ready for the P2P RF feature builder:

      GraphCycleFlag              — 1 if either user is in a Ring finding
      GraphClusterSize            — max CopyPaste cluster size across both users
      SuperNominatorFlag          — 1 if nominator is a SuperNominator outlier
      TransactionalLanguageFlag   — 1 if either user is in a TransactionalLanguage finding

    Returns all-zero dict when no snapshot exists for either user (new users
    with no graph history are treated as having no graph risk signal).
    """
    sql = """
        SELECT TOP 1
               IsInRing, IsSuperNominator,
               IsInCopyPasteCluster, CopyPasteClusterSize,
               HasTransactionalLanguage
        FROM   dbo.UserGraphFlags
        WHERE  TenantId = ? AND UserId = ?
        ORDER  BY AsOfDate DESC
    """
    with _get_conn() as conn:
        cursor = conn.cursor()

        cursor.execute(sql, (tenant_id, nominator_id))
        n_row = cursor.fetchone()

        cursor.execute(sql, (tenant_id, beneficiary_id))
        b_row = cursor.fetchone()

    n_ring  = bool(n_row[0]) if n_row else False
    n_super = bool(n_row[1]) if n_row else False
    n_copy  = int(n_row[3])  if n_row and n_row[2] else 0
    n_trans = bool(n_row[4]) if n_row else False

    b_ring  = bool(b_row[0]) if b_row else False
    b_copy  = int(b_row[3])  if b_row and b_row[2] else 0
    b_trans = bool(b_row[4]) if b_row else False

    return {
        "GraphCycleFlag":            int(n_ring or b_ring),
        "GraphClusterSize":          max(n_copy, b_copy),
        "SuperNominatorFlag":        int(n_super),
        "TransactionalLanguageFlag": int(n_trans or b_trans),
    }


def get_approver_graph_flags(
    tenant_id:     int,
    approver_id:   int,
    nominator_id:  int,
    beneficiary_id: int,
) -> dict:
    """
    Return the latest graph flags for the approver role:

      ApproverAffinityFlag    — approver is in an ApproverAffinity finding
      GraphApproverPairCount  — how many times this approver has approved
                                this exact nominator→beneficiary pair

    Returns all-zero dict when no snapshot exists.
    """
    with _get_conn() as conn:
        cursor = conn.cursor()

        # ApproverAffinityFlag from UserGraphFlags
        cursor.execute("""
            SELECT TOP 1 IsApproverAffinity
            FROM   dbo.UserGraphFlags
            WHERE  TenantId = ? AND UserId = ?
            ORDER  BY AsOfDate DESC
        """, (tenant_id, approver_id))
        a_row = cursor.fetchone()

        # GraphApproverPairCount from ApproverPairFlags
        cursor.execute("""
            SELECT TOP 1 PairApprovalCount
            FROM   dbo.ApproverPairFlags
            WHERE  TenantId      = ?
              AND  ApproverId    = ?
              AND  NominatorId   = ?
              AND  BeneficiaryId = ?
            ORDER  BY AsOfDate DESC
        """, (tenant_id, approver_id, nominator_id, beneficiary_id))
        p_row = cursor.fetchone()

    return {
        "ApproverAffinityFlag":   int(bool(a_row[0])) if a_row else 0,
        "GraphApproverPairCount": int(p_row[0])        if p_row else 0,
    }


# ── Fraud score persistence ───────────────────────────────────────────────────

def save_p2p_fraud_score(
    nomination_id: int,
    fraud_score:   int,
    risk_level:    str,
    warning_flags: str,
) -> None:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            MERGE dbo.P2P_FraudScores AS target
            USING (SELECT ? AS NominationId) AS source
                ON target.NominationId = source.NominationId
            WHEN MATCHED THEN
                UPDATE SET FraudScore = ?, RiskLevel = ?, FraudFlags = ?
            WHEN NOT MATCHED THEN
                INSERT (NominationId, FraudScore, RiskLevel, FraudFlags)
                VALUES (?,            ?,          ?,         ?);
        """, (
            nomination_id,
            fraud_score, risk_level, warning_flags,
            nomination_id, fraud_score, risk_level, warning_flags,
        ))
        conn.commit()


def save_hrbp_fraud_flags(
    nomination_id:        int,
    fraud_score:          int,
    fraud_probability:    float,
    risk_level:           str,
    warning_flags:        str,
    shap_explanations_json: Optional[str],  # JSON list of top-5 SHAP contributions
    feature_summary_json: Optional[str],
) -> None:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO dbo.HRBP_FraudFlags
                (NominationId, FraudScore, FraudProbability, RiskLevel,
                 WarningFlags, TopFeaturesJson, FeatureSummaryJson, CreatedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, GETUTCDATE())
        """, (
            nomination_id, fraud_score, fraud_probability, risk_level,
            warning_flags, shap_explanations_json, feature_summary_json,
        ))
        conn.commit()


# ── Idempotency (dbo.ProcessedEvents) ────────────────────────────────────────

def claim_message(
    message_id:   str,
    event_type:   str,
    nomination_id: Optional[int],
    processed_at: datetime,
) -> bool:
    """
    Insert into dbo.ProcessedEvents. Returns True if already processed.
    Re-claims messages whose prior result was 'error' (allows retry).
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
                "SELECT Result FROM dbo.ProcessedEvents WHERE MessageId = ?",
                (message_id,),
            )
            existing = cursor.fetchone()
            prior    = existing[0] if existing else None

            if prior == "error":
                cursor.execute("DELETE FROM dbo.ProcessedEvents WHERE MessageId = ?",
                               (message_id,))
                cursor.execute("""
                    INSERT INTO dbo.ProcessedEvents
                        (MessageId, EventType, NominationId, ProcessedAt, Result)
                    VALUES (?, ?, ?, ?, 'pending')
                """, (message_id, event_type, nomination_id, processed_at))
                conn.commit()
                return False   # retry allowed

            return True   # already done — skip


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
