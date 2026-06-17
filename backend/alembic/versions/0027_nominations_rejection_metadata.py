"""Add RejectionReason and RejectionActor to dbo.Nominations

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-17

Context
-------
Nominations can be rejected by three different actors:
  1. Fraud Detection — auto-reject at submission time (description/category check)
  2. HRBP Review     — human-in-the-loop rejection after fraud flag
  3. Manager         — approver rejects via the app or email action link

Previously the rejection reason existed only in Service Bus event payloads and
application logs, with no persistent record.  These two columns make the reason
and the rejecting actor queryable and displayable on nomination cards in both
the nominator's History tab and the manager's Approved/Rejected view.

Columns
-------
  RejectionReason  NVARCHAR(1000) NULL — free-text reason supplied by the actor
  RejectionActor   NVARCHAR(200)  NULL — who/what rejected:
                                         "Fraud Detection", "HRBP Review",
                                         or "Manager"

Downgrade
---------
Drops both columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :t AND COLUMN_NAME = :c AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name, "c": column_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "Nominations", "RejectionReason"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Nominations ADD RejectionReason NVARCHAR(1000) NULL"
        ))
    if not _column_exists(conn, "Nominations", "RejectionActor"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Nominations ADD RejectionActor NVARCHAR(200) NULL"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "Nominations", "RejectionActor"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Nominations DROP COLUMN RejectionActor"
        ))
    if _column_exists(conn, "Nominations", "RejectionReason"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Nominations DROP COLUMN RejectionReason"
        ))
