"""Add ProcessedEvents table and ApproverNotifiedAt to Nominations

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-01

Context
-------
Supports the move to an event-driven architecture where the Award Auxiliary
Service (a Container App worker) consumes Service Bus messages and sends emails
asynchronously, decoupled from the FastAPI request lifecycle.

Changes
-------

1. New table: dbo.ProcessedEvents
   Idempotency table — prevents duplicate processing when Service Bus redelivers
   a message (at-least-once delivery guarantee).  The Service Bus MessageId is
   the primary key; before processing any event the worker inserts a row here.
   If the insert fails with a PK violation the message is already processed and
   is safely discarded ('skipped').

   The table is intentionally generic (EventType + NominationId columns) so all
   future event types (payroll, HR sync, Entra sync, exports, etc.) share one
   idempotency mechanism without touching the business tables.  NominationId is
   nullable for events that are not tied to a specific nomination.

   Columns:
     MessageId    NVARCHAR(128)  PK — Service Bus message ID (GUID, 36 chars max)
     EventType    NVARCHAR(100)  e.g. 'nomination.created', 'nomination.approved'
     NominationId INT            NULL — FK to the relevant nomination (when applicable)
     ProcessedAt  DATETIME2      UTC timestamp; written by the worker at insert time
     Result       NVARCHAR(20)   'success' | 'skipped' | 'error'

2. New column: dbo.Nominations.ApproverNotifiedAt (DATETIME2, nullable)
   Legitimate business data — records when the approver first received the
   nomination request email.  This is part of the nomination lifecycle, distinct
   from idempotency tracking.

   Answers operational questions such as:
     - How long did it take from nomination submission to approver notification?
     - Which nominations have an approver who has not yet been notified?
     - SLA reporting: approver notified within N minutes of submission?

   NULL = approver not yet notified (event in flight or worker not yet run).
   Populated by the auxiliary worker after successful SMTP hand-off for the
   'nomination.created' event type.

Downgrade
---------
Drops ApproverNotifiedAt from dbo.Nominations, then drops dbo.ProcessedEvents.
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Helpers (same pattern as 0001) ────────────────────────────────────────────

def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t"
    ), {"t": table_name}).fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    return conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {"t": table_name, "c": column_name}).fetchone() is not None


# ── Upgrade ───────────────────────────────────────────────────────────────────

def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. ProcessedEvents table ─────────────────────────────────────────────
    if not _table_exists(conn, "ProcessedEvents"):
        op.create_table(
            "ProcessedEvents",
            sa.Column("MessageId",    sa.String(128),  nullable=False),
            sa.Column("EventType",    sa.String(100),  nullable=False),
            sa.Column("NominationId", sa.Integer(),    nullable=True),
            sa.Column("ProcessedAt",  sa.DateTime(),   nullable=False),
            sa.Column("Result",       sa.String(20),   nullable=False),
            sa.PrimaryKeyConstraint("MessageId", name="PK_ProcessedEvents"),
            schema="dbo",
        )

        # Index on EventType + ProcessedAt — supports queries like
        # "show all failed nomination.created events this week"
        conn.execute(sa.text(
            "CREATE INDEX ix_processedevents_eventtype "
            "ON dbo.ProcessedEvents (EventType, ProcessedAt DESC)"
        ))

        # Index on NominationId — supports queries like
        # "show all events processed for nomination 42"
        conn.execute(sa.text(
            "CREATE INDEX ix_processedevents_nominationid "
            "ON dbo.ProcessedEvents (NominationId) "
            "WHERE NominationId IS NOT NULL"
        ))

    # ── 2. Nominations — ApproverNotifiedAt ──────────────────────────────────
    # Business data: when did the approver first receive the nomination request?
    # Part of the nomination lifecycle — not event-system bookkeeping.
    # Set by the auxiliary worker after SMTP hand-off for 'nomination.created'.
    if not _column_exists(conn, "Nominations", "ApproverNotifiedAt"):
        op.add_column(
            "Nominations",
            sa.Column("ApproverNotifiedAt", sa.DateTime(), nullable=True),
            schema="dbo",
        )

    conn.execute(sa.text("COMMIT"))


# ── Downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    conn = op.get_bind()

    if _column_exists(conn, "Nominations", "ApproverNotifiedAt"):
        op.drop_column("Nominations", "ApproverNotifiedAt", schema="dbo")

    if _table_exists(conn, "ProcessedEvents"):
        conn.execute(sa.text(
            "DROP INDEX IF EXISTS ix_processedevents_nominationid ON dbo.ProcessedEvents"
        ))
        conn.execute(sa.text(
            "DROP INDEX IF EXISTS ix_processedevents_eventtype ON dbo.ProcessedEvents"
        ))
        op.drop_table("ProcessedEvents", schema="dbo")
