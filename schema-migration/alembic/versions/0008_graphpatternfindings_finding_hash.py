"""Add FindingHash column to GraphPatternFindings for idempotent inserts

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-14

Context
-------
The fraud analytics job runs weekly and may also be triggered manually.
Without deduplication, re-running the job inserts identical findings with
a new RunId, polluting the table and the Integrity tab run selector.

A deterministic SHA-256 fingerprint (truncated to 64 hex chars) is computed
in graph_pattern_detector.py from:

    TenantId | PatternType | AffectedUsers (sorted JSON) | NominationIds (sorted JSON)

This uniquely identifies a finding's content independent of when it was
detected.  The detector checks existing hashes before inserting, so:

  - Same finding (same users, same nominations) → never inserted twice
  - Evolved finding (same ring, new nominations added) → new hash → inserted
  - New finding → inserted as normal

The unique index on (TenantId, FindingHash) enforces this at the DB level
as a safety net, in addition to the Python-side pre-check.

FindingHash is nullable to allow the column to be added to a table that
already has rows (existing rows get NULL and are effectively excluded from
the dedup logic — the detector will re-evaluate them naturally on the next
run when the window rolls forward).
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
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


def _index_exists(conn, index: str, table: str) -> bool:
    row = conn.execute(sa.text("""
        SELECT 1 FROM sys.indexes
        WHERE  name      = :i
          AND  object_id = OBJECT_ID('dbo.' + :t)
    """), {"i": index, "t": table}).fetchone()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()

    # Add FindingHash column (nullable — existing rows get NULL)
    if not _column_exists(conn, "GraphPatternFindings", "FindingHash"):
        conn.execute(sa.text("""
            ALTER TABLE dbo.GraphPatternFindings
            ADD FindingHash NVARCHAR(64) NULL
        """))

    # Unique index on (TenantId, FindingHash) — enforces dedup at DB level.
    # Filtered to WHERE FindingHash IS NOT NULL so existing NULL rows are
    # excluded and don't violate the uniqueness constraint.
    if not _index_exists(conn, "ux_graphpatternfindings_hash", "GraphPatternFindings"):
        conn.execute(sa.text("""
            CREATE UNIQUE INDEX ux_graphpatternfindings_hash
            ON dbo.GraphPatternFindings (TenantId, FindingHash)
            WHERE FindingHash IS NOT NULL
        """))


def downgrade() -> None:
    conn = op.get_bind()

    if _index_exists(conn, "ux_graphpatternfindings_hash", "GraphPatternFindings"):
        conn.execute(sa.text("""
            DROP INDEX ux_graphpatternfindings_hash
            ON dbo.GraphPatternFindings
        """))

    if _column_exists(conn, "GraphPatternFindings", "FindingHash"):
        conn.execute(sa.text("""
            ALTER TABLE dbo.GraphPatternFindings
            DROP COLUMN FindingHash
        """))
