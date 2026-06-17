"""
Database access for the auxiliary worker.

Uses the same pyodbc + SQL auth pattern as the backend's sqlhelper.py.
Exposes only the queries the worker needs:
  - get_nomination_details()        — full nomination data for email templates
  - claim_message()                 — idempotency insert into ProcessedEvents
  - update_processed_event_result() — update result/error after handling
  - set_approver_notified()         — stamp ApproverNotifiedAt on Nominations
  - get_hrbp_users()                — HRBP role holders for a tenant
  - get_hrbp_fraud_flags()          — ML inference snapshot for HRBP emails

Fraud detection helpers (used by nomination_submitted handler):
  - get_nominator_history()         — past nominations sent by a user
  - get_beneficiary_history()       — past nominations received by a user
  - get_approver_history()          — past approvals by a user
  - check_reciprocal_nomination()   — has B ever nominated A?
  - get_pair_nomination_count()     — how many times has A nominated B?
  - get_beneficiary_descriptions()  — past descriptions written BY the beneficiary
  - set_nomination_status()         — move nomination to Pending / PendingHRBPReview
  - save_p2p_fraud_score()          — upsert into dbo.P2P_FraudScores
  - save_hrbp_fraud_flags()         — insert into dbo.HRBP_FraudFlags
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import pyodbc

logger = logging.getLogger("auxiliary.db")

# ── Connection string ─────────────────────────────────────────────────────────
# Secrets are injected by ACA from Key Vault references at container startup.
# The worker always uses SQL auth (the managed identity is used for Service Bus
# and Key Vault, not for the SQL Server in this setup).
_SERVER   = os.environ["SQL_SERVER"]
_DATABASE = os.environ["SQL_DATABASE"]
_USER     = os.environ["SQL_USER"]
_PASSWORD = os.environ["SQL_PASSWORD"]
_DRIVER   = os.getenv("DB_DRIVER", "{ODBC Driver 18 for SQL Server}")

_CONNECTION_STRING = (
    f"Driver={_DRIVER};"
    f"Server={_SERVER};"
    f"Database={_DATABASE};"
    f"UID={_USER};"
    f"PWD={_PASSWORD};"
    f"Encrypt=yes;"
    f"TrustServerCertificate=no;"
)


@contextmanager
def _get_conn():
    """Open a connection, yield it, and close it — even on exception."""
    conn = pyodbc.connect(_CONNECTION_STRING)
    try:
        yield conn
    finally:
        conn.close()


# ── Nomination queries ────────────────────────────────────────────────────────

def get_nomination_details(nomination_id: int) -> Optional[dict]:
    """
    Fetch all data needed to build notification emails for a nomination.
    Returns None if the nomination does not exist.

    This is the authoritative read — the worker always fetches fresh data from
    the DB rather than trusting the event payload, ensuring consistency even if
    the payload was stale or truncated.
    """
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
                n.RejectionReason,
                n.RejectionActor
            FROM  dbo.Nominations n
            INNER JOIN dbo.Users nominator   ON n.NominatorId   = nominator.UserId
            INNER JOIN dbo.Users beneficiary ON n.BeneficiaryId = beneficiary.UserId
            INNER JOIN dbo.Users approver    ON n.ApproverId    = approver.UserId
            LEFT JOIN  dbo.nomination_categories nc ON nc.id    = n.CategoryId
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
        "beneficiary_id":       int(row[9]),   # needed by payout_submit handler
        "beneficiary_name":     row[10],
        "beneficiary_email":    row[11],
        "approver_name":        row[12],
        "approver_email":       row[13],
        "category_description": row[14],       # None for tenants without categories
        "tenant_id":            int(row[15]),
        "rejection_reason":     row[16],       # None unless nomination is Rejected
        "rejection_actor":      row[17],
    }


def set_approver_notified(nomination_id: int) -> None:
    """
    Stamp ApproverNotifiedAt on the nomination row.
    Called by the nomination_created handler after successful email send.
    This is business lifecycle data — when did the approver first receive
    the nomination request?
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE dbo.Nominations
            SET    ApproverNotifiedAt = GETUTCDATE()
            WHERE  NominationId = ?
              AND  ApproverNotifiedAt IS NULL   -- only stamp once
        """, (nomination_id,))
        conn.commit()
        if cursor.rowcount == 0:
            logger.debug(
                "ApproverNotifiedAt already set — no update needed",
                extra={"nomination_id": nomination_id}
            )


# ── HRBP review workflow ──────────────────────────────────────────────────────

def get_tenant_portal_url(tenant_id: int) -> str:
    """
    Return the tenant's frontend portal URL from dbo.Tenants.Site_URL.
    Falls back to a generic URL if Site_URL is not set.

    To configure: UPDATE dbo.Tenants SET Site_URL = 'https://awards.example.com'
                  WHERE TenantId = <id>
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Site_URL FROM dbo.Tenants WHERE TenantId = ?",
            (tenant_id,)
        )
        row = cursor.fetchone()
    return row[0] if row and row[0] else None


def get_tenant_certificate_config(tenant_id: int) -> dict:
    """
    Read per-tenant award-certificate settings from dbo.Tenants.certificate_config.

    Returns a dict with keys {enabled, attach_to_beneficiary, template_blob}.
    A NULL column or invalid JSON yields all-defaults → feature OFF, so the
    worker never attaches a certificate unless the tenant has opted in.

    Kept in sync with the backend's CertificateConfig (utils/sqlhelper2.py).
    """
    import json

    defaults = {"enabled": False, "attach_to_beneficiary": False,
                "template_blob": "default_certificate.png"}

    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT certificate_config FROM dbo.Tenants WHERE TenantId = ?",
            (tenant_id,)
        )
        row = cursor.fetchone()

    raw = row[0] if row else None
    if not raw:
        return defaults

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid certificate_config JSON for tenant %d — using defaults", tenant_id)
        return defaults

    return {
        "enabled":               bool(data.get("enabled", defaults["enabled"])),
        "attach_to_beneficiary": bool(data.get("attach_to_beneficiary", defaults["attach_to_beneficiary"])),
        "template_blob":         str(data.get("template_blob", defaults["template_blob"])),
    }


DEFAULT_TEMPLATE_TENANT_ID = 1   # canonical org holding the system default rows


def get_tenant_lang(tenant_id: int) -> str:
    """Tenant base language code ('en', 'ko', ...) from dbo.Tenants.Config.locale; 'en' on any miss."""
    import json
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT Config FROM dbo.Tenants WHERE TenantId = ?", (tenant_id,))
        row = cursor.fetchone()
    raw = row[0] if row else None
    if not raw:
        return "en"
    try:
        locale = json.loads(raw).get("locale") or "en"
    except (json.JSONDecodeError, TypeError):
        return "en"
    return (locale.split("-")[0].lower() or "en")


def get_email_template_candidates(template_key: str, tenant_id: int, lang: str) -> list:
    """Active template rows for the resolver to rank: this tenant + the default
    tenant, in this language + English. Returns [(TenantId, Lang, Subject, BodyTemplate), ...]."""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TenantId, Lang, Subject, BodyTemplate
            FROM dbo.EmailTemplates
            WHERE TemplateKey = ? AND Active = 1
              AND TenantId IN (?, ?) AND Lang IN (?, 'en')
            """,
            (template_key, tenant_id, DEFAULT_TEMPLATE_TENANT_ID, lang),
        )
        rows = cursor.fetchall()
    return [(int(r[0]), r[1], r[2], r[3]) for r in rows]


def get_tenant_fallback_admin(tenant_id: int) -> Optional[dict]:
    """
    Return the fallback admin contact for a tenant when no HRBP users are
    configured.  Reads dbo.Tenants.fallback_admin_email directly.

    Returns a dict {full_name, email} or None if the column is NULL.

    To configure: UPDATE dbo.Tenants
                  SET fallback_admin_email = 'admin@example.com'
                  WHERE TenantId = <id>
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT fallback_admin_email, TenantName "
            "FROM dbo.Tenants WHERE TenantId = ?",
            (tenant_id,)
        )
        row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return {"full_name": f"{row[1]} Administrator", "email": row[0]}


def get_hrbp_users(tenant_id: int) -> list[dict]:
    """Return all users with the HRBP role for a given tenant."""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.UserId,
                   u.FirstName + ' ' + u.LastName AS FullName,
                   u.userEmail
            FROM   dbo.UserRoles ur
            JOIN   dbo.Users u ON u.UserId = ur.UserId
            WHERE  ur.Role    = 'HRBP'
              AND  u.TenantId = ?
        """, (tenant_id,))
        return [
            {"user_id": row[0], "full_name": row[1], "email": row[2]}
            for row in cursor.fetchall()
        ]


def get_hrbp_fraud_flags(nomination_id: int) -> Optional[dict]:
    """Return the HRBP_FraudFlags snapshot for a nomination, or None."""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT FraudScore, FraudProbability, RiskLevel,
                   WarningFlags, TopFeaturesJson, FeatureSummaryJson
            FROM   dbo.HRBP_FraudFlags
            WHERE  NominationId = ?
        """, (nomination_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "fraud_score":       row[0],
            "fraud_probability": row[1],
            "risk_level":        row[2],
            "warning_flags":     row[3] or "",
            "top_features_json": row[4],
            "feature_summary":   row[5],
        }


# ── ProcessedEvents (idempotency) ─────────────────────────────────────────────

def claim_message(
    message_id: str,
    event_type: str,
    nomination_id: Optional[int],
    processed_at: datetime,
) -> bool:
    """
    Attempt to insert a row into dbo.ProcessedEvents.

    Returns True  if the message was already processed (PK violation caught).
    Returns False if the insert succeeded (message is new → proceed with handling).

    The result column is set to 'pending' initially and updated to
    'success' or 'error' by update_processed_event_result() after handling.
    """
    logger.debug(
        "claim_message called",
        extra={
            "message_id":    message_id,
            "event_type":    event_type,
            "nomination_id": nomination_id,
            "processed_at":  processed_at.isoformat(),
        },
    )
    with _get_conn() as conn:
        cursor = conn.cursor()
        try:
            logger.debug("Executing INSERT into dbo.ProcessedEvents")
            cursor.execute("""
                INSERT INTO dbo.ProcessedEvents
                    (MessageId, EventType, NominationId, ProcessedAt, Result)
                VALUES (?, ?, ?, ?, 'pending')
            """, (message_id, event_type, nomination_id, processed_at))
            logger.debug("INSERT executed, rowcount=%d — committing", cursor.rowcount)
            conn.commit()
            logger.info(
                "ProcessedEvents row claimed (new message)",
                extra={"message_id": message_id, "event_type": event_type},
            )
            return False  # new message — proceed

        except pyodbc.IntegrityError:
            # PK violation — row already exists.  Check whether the previous
            # attempt errored: if so, delete and re-insert so the message is
            # retried.  Only block if the previous result was success/skipped.
            cursor.execute(
                "SELECT Result FROM dbo.ProcessedEvents WHERE MessageId = ?",
                (message_id,)
            )
            existing = cursor.fetchone()
            prior_result = existing[0] if existing else None

            if prior_result == 'error':
                # Previous attempt failed — allow retry by reclaiming the row.
                cursor.execute(
                    "DELETE FROM dbo.ProcessedEvents WHERE MessageId = ?",
                    (message_id,)
                )
                cursor.execute("""
                    INSERT INTO dbo.ProcessedEvents
                        (MessageId, EventType, NominationId, ProcessedAt, Result)
                    VALUES (?, ?, ?, ?, 'pending')
                """, (message_id, event_type, nomination_id, processed_at))
                conn.commit()
                logger.warning(
                    "claim_message: prior attempt errored — reclaimed for retry",
                    extra={"message_id": message_id, "event_type": event_type},
                )
                return False  # proceed with retry

            # Result is success, skipped, or pending (another worker is handling
            # it) — do not reprocess.
            logger.info(
                "claim_message: already processed (result=%s) — skipping",
                prior_result,
                extra={"message_id": message_id, "prior_result": prior_result},
            )
            return True

        except Exception as exc:
            # Any other DB error (e.g. table missing, column mismatch, connection drop).
            logger.exception(
                "claim_message: unexpected %s — ProcessedEvents write failed",
                type(exc).__name__,
                extra={"message_id": message_id, "event_type": event_type},
            )
            raise


def update_processed_event_result(
    message_id: str,
    result: str,             # 'success' | 'error'
    error: Optional[str] = None,
) -> None:
    """
    Update the Result (and optionally ErrorMessage) on an existing
    ProcessedEvents row after the handler completes.
    """
    logger.debug(
        "update_processed_event_result called",
        extra={"message_id": message_id, "result": result},
    )
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE dbo.ProcessedEvents
            SET    Result = ?
            WHERE  MessageId = ?
        """, (result, message_id))
        conn.commit()
        if cursor.rowcount == 0:
            logger.warning(
                "update_processed_event_result: UPDATE matched 0 rows — "
                "ProcessedEvents row missing for message_id=%s (claim_message may have failed silently)",
                message_id,
                extra={"message_id": message_id, "result": result},
            )
        else:
            logger.debug(
                "ProcessedEvents row updated",
                extra={"message_id": message_id, "result": result, "rowcount": cursor.rowcount},
            )

    if error:
        logger.error(
            "ProcessedEvent recorded as error",
            extra={"message_id": message_id, "error": error}
        )


# ── Fraud detection — history lookups ─────────────────────────────────────────

def get_nominator_history(nominator_id: int) -> list[tuple]:
    """All previous nominations sent by this nominator."""
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
    """All previous nominations received by this beneficiary."""
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
    """All previous nominations approved by this approver."""
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
    """True if the beneficiary has ever nominated the nominator back."""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM dbo.Nominations
            WHERE  NominatorId   = ?
              AND  BeneficiaryId = ?
        """, (beneficiary_id, nominator_id))
        row = cursor.fetchone()
        return (row[0] > 0) if row else False


def get_pair_nomination_count(nominator_id: int, beneficiary_id: int) -> int:
    """How many times has this nominator nominated this beneficiary?"""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM dbo.Nominations
            WHERE  NominatorId   = ?
              AND  BeneficiaryId = ?
        """, (nominator_id, beneficiary_id))
        row = cursor.fetchone()
        return row[0] if row else 0


def get_beneficiary_descriptions(beneficiary_id: int) -> list[str]:
    """
    Past NominationDescriptions WRITTEN BY the beneficiary (as nominator).
    Used to build their 'voice' embedding for semantic similarity scoring.
    Capped at 20 most-recent to bound encoding time.
    """
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


# ── Fraud detection — write functions ─────────────────────────────────────────

def set_nomination_status(nomination_id: int, new_status: str) -> None:
    """Move a nomination to a new status (e.g. Pending, PendingHRBPReview)."""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE dbo.Nominations SET Status = ? WHERE NominationId = ?
        """, (new_status, nomination_id))
        conn.commit()
        logger.info(
            "Nomination status updated",
            extra={"nomination_id": nomination_id, "new_status": new_status},
        )


def save_p2p_fraud_score(
    nomination_id: int,
    fraud_score:   int,
    risk_level:    str,
    warning_flags: str,
) -> None:
    """Upsert the P2P fraud score for a nomination."""
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
    top_features_json:    Optional[str],
    feature_summary_json: Optional[str],
) -> None:
    """Insert the HRBP fraud flag snapshot (used by the HRBP review queue UI)."""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO dbo.HRBP_FraudFlags
                (NominationId, FraudScore, FraudProbability, RiskLevel,
                 WarningFlags, TopFeaturesJson, FeatureSummaryJson, CreatedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, GETUTCDATE())
        """, (
            nomination_id, fraud_score, fraud_probability, risk_level,
            warning_flags, top_features_json, feature_summary_json,
        ))
        conn.commit()
