"""Add TotalAmount column to GraphPatternFindings

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-16

Context
-------
The fraud analytics job now computes the total approved/paid nomination amount
for each finding and stores it in TotalAmount.  This column drives
financial-exposure-based severity in detect_rings() and gives the Integrity
tab a dollar figure for each pattern cluster.

TotalAmount is nullable so the migration is safe on a live table — existing
rows keep NULL, and the detector populates it for all new findings.

Detectors that have no associated nominations (Desert, HiddenCandidate) store
0.  All other detectors store the sum of Amount across the finding's
NominationIds.
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(sa.text("""
        SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE  TABLE_SCHEMA = 'dbo'
          AND  TABLE_NAME   = :t
          AND  COLUMN_NAME  = :c
    """), {"t": table, "c": column}).fetchone()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()

    if not _column_exists(conn, "GraphPatternFindings", "TotalAmount"):
        conn.execute(sa.text("""
            ALTER TABLE dbo.GraphPatternFindings
            ADD TotalAmount INT NULL
        """))


def downgrade() -> None:
    conn = op.get_bind()

    if _column_exists(conn, "GraphPatternFindings", "TotalAmount"):
        conn.execute(sa.text("""
            ALTER TABLE dbo.GraphPatternFindings
            DROP COLUMN TotalAmount
        """))
