"""Add PaymentRef and PaymentSubmittedAt to Nominations

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-20

Context
-------
Adds two columns to support the Workday payroll integration flow:

  PaymentRef           NVARCHAR(100) NULL
      The reference ID returned synchronously by Workday_Proxy (or real
      Workday) when a payout is submitted.  Stored immediately when the
      auxiliary app receives the POST /payouts response, before the async
      PayoutAccepted confirmation arrives.  Serves as the correlation key
      that links the Service Bus PayoutAccepted event back to the right
      nomination.  Example: "WD-2026-00123".

  PaymentSubmittedAt   DATETIME2 NULL
      UTC timestamp of when the payment was handed off to Workday_Proxy.
      Together with the existing PayedDate (= PaymentConfirmedAt) this
      gives the full payment lifecycle timeline:
        ApprovedDate        — manager approved
        PaymentSubmittedAt  — sent to payroll
        PayedDate           — payroll confirmed

Status lifecycle
----------------
  Pending → Approved → PaymentSubmitted → Paid
                     ↘ PaymentFailed  (future — real Workday rejection)

  PaymentSubmitted and PaymentFailed are valid Status values as of this
  migration.  No CHECK constraint is added — SQL Server does not enforce
  an exhaustive enum and new statuses should not require a schema change.
"""

import sqlalchemy as sa
from alembic import op

revision      = "0012"
down_revision = "0011"
branch_labels = None
depends_on    = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _column_exists("Nominations", "PaymentRef"):
        op.add_column(
            "Nominations",
            sa.Column("PaymentRef", sa.Unicode(100), nullable=True),
        )

    if not _column_exists("Nominations", "PaymentSubmittedAt"):
        op.add_column(
            "Nominations",
            sa.Column("PaymentSubmittedAt", sa.DateTime(), nullable=True),
        )

    # Index to support lookups by PaymentRef (webhook callback needs to find
    # the nomination from the paymentRef returned by Workday_Proxy).
    conn = op.get_bind()
    index_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM sys.indexes "
            "WHERE object_id = OBJECT_ID('dbo.Nominations') "
            "  AND name = 'IX_Nominations_PaymentRef'"
        )
    ).fetchone()
    if not index_exists:
        op.create_index(
            "IX_Nominations_PaymentRef",
            "Nominations",
            ["PaymentRef"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    index_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM sys.indexes "
            "WHERE object_id = OBJECT_ID('dbo.Nominations') "
            "  AND name = 'IX_Nominations_PaymentRef'"
        )
    ).fetchone()
    if index_exists:
        op.drop_index("IX_Nominations_PaymentRef", table_name="Nominations")

    if _column_exists("Nominations", "PaymentSubmittedAt"):
        op.drop_column("Nominations", "PaymentSubmittedAt")

    if _column_exists("Nominations", "PaymentRef"):
        op.drop_column("Nominations", "PaymentRef")
