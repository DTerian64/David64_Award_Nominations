"""
Database access for the integrity-check worker.

Focused subset of queries needed by handler.py and fraud_check.py:

  Idempotency:
    claim_message()                 — insert into dbo.ProcessedEvents
    update_processed_event_result() — update result after handling

  Nomination data:
    get_nomination_details()        — full nomination for fraud feature engineering
    set_nomination_status()         — move to Pending / PendingHRBPReview

  Fraud history lookups (called by fraud_check.py):
    get_nominator_history()         — past nominations sent by a user
    get_beneficiary_history()       — past nominations received by a user
    get_approver_history()          — past approvals by a user
    check_reciprocal_nomination()   — has B ever nominated A?
    get_pair_nomination_count()     — how many times has A nominated B?
    get_beneficiary_descriptions()  — past descriptions written BY the beneficiary

  Fraud score persistence:
    save_p2p_fraud_score()          — upsert into dbo.P2P_FraudScores
    save_hrbp_fraud_flags()         — insert into dbo.HRBP_FraudFlags
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import pyodbc

logger = logging.getLogger("integrity_check.db")

# ── Connection string ─────────────────────────────────────────────────────────
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
    conn = pyodbc.connect(_CONNECTION_STRING)
    try:
        yield conn
    finally:
        conn.close()


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
    top_features_json:    Optional[str],
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
            warning_flags, top_features_json, feature_summary_json,
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
