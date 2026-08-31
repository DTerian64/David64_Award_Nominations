"""Convert GraphPatternFindings text columns to NVARCHAR (Unicode)

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-14

Context
-------
Migration 0006 created dbo.GraphPatternFindings with VARCHAR columns for
PatternType, Severity, AffectedUsers, NominationIds, and Detail.
VARCHAR uses the database's default collation (typically Latin-1) and
cannot store characters outside the basic ASCII/Latin range.

The Detail column contains human-readable text built from user names and
nomination descriptions. In international deployments user names frequently
contain non-Latin characters (CJK, Cyrillic, Arabic, accented Latin, etc.)
that would be silently corrupted or cause insertion errors with VARCHAR.

This migration ALTERs those columns to their NVARCHAR equivalents, which
store UTF-16 and support the full Unicode character set — matching the
pattern used by NominationDescription, FirstName, LastName, and Title
throughout the rest of the schema.

Column mapping
--------------
  PatternType  VARCHAR(50)       → NVARCHAR(50)
  Severity     VARCHAR(20)       → NVARCHAR(20)
  AffectedUsers VARCHAR(MAX)/Text → NVARCHAR(MAX)
  NominationIds VARCHAR(MAX)/Text → NVARCHAR(MAX)
  Detail       VARCHAR(1000)     → NVARCHAR(1000)
  RunId        VARCHAR(36)       → NVARCHAR(36)   (GUID — ASCII-safe but
                                                    kept consistent)
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_type(conn, table: str, column: str) -> str | None:
    """Return the current DATA_TYPE for a column, or None if not found."""
    row = conn.execute(sa.text("""
        SELECT DATA_TYPE
        FROM   INFORMATION_SCHEMA.COLUMNS
        WHERE  TABLE_SCHEMA = 'dbo'
          AND  TABLE_NAME   = :t
          AND  COLUMN_NAME  = :c
    """), {"t": table, "c": column}).fetchone()
    return row[0] if row else None


def upgrade() -> None:
    conn = op.get_bind()

    # SQL Server refuses to ALTER a column that any index references.
    # Drop the two indexes that cover PatternType, Severity, DetectedAt, RunId,
    # do all the ALTERs, then recreate them.

    conn.execute(sa.text(
        "DROP INDEX IF EXISTS ix_graphpatternfindings_tenant_pattern "
        "ON dbo.GraphPatternFindings"
    ))
    conn.execute(sa.text(
        "DROP INDEX IF EXISTS ix_graphpatternfindings_runid "
        "ON dbo.GraphPatternFindings"
    ))

    # Each ALTER is guarded: only run if the column is still VARCHAR/TEXT.
    # Makes the migration safe to re-run after a partial failure.
    alterations = [
        ("PatternType",   "NVARCHAR(50)",   "NOT NULL"),
        ("Severity",      "NVARCHAR(20)",   "NOT NULL"),
        ("AffectedUsers", "NVARCHAR(MAX)",  "NULL"),
        ("NominationIds", "NVARCHAR(MAX)",  "NULL"),
        ("Detail",        "NVARCHAR(1000)", "NULL"),
        ("RunId",         "NVARCHAR(36)",   "NOT NULL"),
    ]

    for col, new_type, nullability in alterations:
        current = _column_type(conn, "GraphPatternFindings", col)
        if current and current.upper() in ("VARCHAR", "TEXT"):
            conn.execute(sa.text(
                f"ALTER TABLE dbo.GraphPatternFindings "
                f"ALTER COLUMN [{col}] {new_type} {nullability}"
            ))

    # Recreate indexes
    conn.execute(sa.text(
        "CREATE INDEX ix_graphpatternfindings_tenant_pattern "
        "ON dbo.GraphPatternFindings (TenantId, PatternType, DetectedAt DESC)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX ix_graphpatternfindings_runid "
        "ON dbo.GraphPatternFindings (RunId)"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text(
        "DROP INDEX IF EXISTS ix_graphpatternfindings_tenant_pattern "
        "ON dbo.GraphPatternFindings"
    ))
    conn.execute(sa.text(
        "DROP INDEX IF EXISTS ix_graphpatternfindings_runid "
        "ON dbo.GraphPatternFindings"
    ))

    alterations = [
        ("PatternType",   "VARCHAR(50)",   "NOT NULL"),
        ("Severity",      "VARCHAR(20)",   "NOT NULL"),
        ("AffectedUsers", "VARCHAR(MAX)",  "NULL"),
        ("NominationIds", "VARCHAR(MAX)",  "NULL"),
        ("Detail",        "VARCHAR(1000)", "NULL"),
        ("RunId",         "VARCHAR(36)",   "NOT NULL"),
    ]

    for col, old_type, nullability in alterations:
        current = _column_type(conn, "GraphPatternFindings", col)
        if current and current.upper() in ("NVARCHAR",):
            conn.execute(sa.text(
                f"ALTER TABLE dbo.GraphPatternFindings "
                f"ALTER COLUMN [{col}] {old_type} {nullability}"
            ))

    conn.execute(sa.text(
        "CREATE INDEX ix_graphpatternfindings_tenant_pattern "
        "ON dbo.GraphPatternFindings (TenantId, PatternType, DetectedAt DESC)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX ix_graphpatternfindings_runid "
        "ON dbo.GraphPatternFindings (RunId)"
    ))
