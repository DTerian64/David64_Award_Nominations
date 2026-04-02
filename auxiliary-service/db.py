"""
Database access for the auxiliary worker.

Uses the same pyodbc + SQL auth pattern as the backend's sqlhelper.py.
Exposes only the queries the worker needs:
  - get_nomination_details()        — full nomination data for email templates
  - claim_message()                 — idempotency insert into ProcessedEvents
  - update_processed_event_result() — update result/error after handling
  - set_approver_notified()         — stamp ApproverNotifiedAt on Nominations
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
                n.DollarAmount,
                n.NominationDescription,
                n.Status,
                n.ApproverId,
                nominator.UserId          AS NominatorId,
                nominator.FirstName + ' ' + nominator.LastName AS NominatorName,
                nominator.userEmail       AS NominatorEmail,
                beneficiary.FirstName + ' ' + beneficiary.LastName AS BeneficiaryName,
                beneficiary.userEmail     AS BeneficiaryEmail,
                approver.FirstName + ' ' + approver.LastName AS ApproverName,
                approver.userEmail        AS ApproverEmail
            FROM  dbo.Nominations n
            INNER JOIN dbo.Users nominator  ON n.NominatorId  = nominator.UserId
            INNER JOIN dbo.Users beneficiary ON n.BeneficiaryId = beneficiary.UserId
            INNER JOIN dbo.Users approver   ON n.ApproverId   = approver.UserId
            WHERE n.NominationId = ?
        """, (nomination_id,))
        row = cursor.fetchone()

    if not row:
        return None

    return {
        "nomination_id":    int(row[0]),
        "dollar_amount":    float(row[1]),
        "description":      row[2],
        "status":           row[3],
        "approver_id":      int(row[4]),
        "nominator_id":     int(row[5]),
        "nominator_name":   row[6],
        "nominator_email":  row[7],
        "beneficiary_name": row[8],
        "beneficiary_email": row[9],
        "approver_name":    row[10],
        "approver_email":   row[11],
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
    with _get_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO dbo.ProcessedEvents
                    (MessageId, EventType, NominationId, ProcessedAt, Result)
                VALUES (?, ?, ?, ?, 'pending')
            """, (message_id, event_type, nomination_id, processed_at))
            conn.commit()
            return False  # new message — proceed
        except pyodbc.IntegrityError:
            # PK violation → already processed
            return True


def update_processed_event_result(
    message_id: str,
    result: str,             # 'success' | 'error'
    error: Optional[str] = None,
) -> None:
    """
    Update the Result (and optionally ErrorMessage) on an existing
    ProcessedEvents row after the handler completes.
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE dbo.ProcessedEvents
            SET    Result = ?
            WHERE  MessageId = ?
        """, (result, message_id))
        conn.commit()

    if error:
        logger.error(
            "ProcessedEvent recorded as error",
            extra={"message_id": message_id, "error": error}
        )
