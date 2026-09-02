"""Store Graph detector importance order in each policy version.

Revision ID: 0050
Revises: 0049
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


_ORDER = {
    "Ring": 1,
    "SuperNominator": 2,
    "CopyPaste": 3,
    "HiddenCandidate": 4,
    "Desert": 5,
    "TransactionalLanguage": 6,
}


def _column_exists(table: str, column: str) -> bool:
    return op.get_bind().execute(sa.text("""
        SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=:table AND COLUMN_NAME=:column
    """), {"table": table, "column": column}).fetchone() is not None


def upgrade() -> None:
    if not _column_exists("GraphScoringPatternParameters", "DisplayOrder"):
        op.execute("""
            ALTER TABLE dbo.GraphScoringPatternParameters
            ADD DisplayOrder SMALLINT NULL;
        """)

    case_lines = " ".join(
        f"WHEN '{pattern}' THEN {display_order}"
        for pattern, display_order in _ORDER.items()
    )
    op.execute(f"""
        UPDATE dbo.GraphScoringPatternParameters
        SET DisplayOrder = CASE PatternType {case_lines} ELSE 999 END
        WHERE DisplayOrder IS NULL;
    """)
    op.execute("""
        ALTER TABLE dbo.GraphScoringPatternParameters
        ALTER COLUMN DisplayOrder SMALLINT NOT NULL;
    """)
    op.execute("""
        ALTER TABLE dbo.GraphScoringPatternParameters
        ADD CONSTRAINT CK_GraphScoringPatternParameters_DisplayOrder
        CHECK (DisplayOrder > 0);
    """)
    op.execute("""
        CREATE UNIQUE INDEX UX_GraphScoringPatternParameters_DisplayOrder
        ON dbo.GraphScoringPatternParameters(PolicyId, DisplayOrder);
    """)


def downgrade() -> None:
    if _column_exists("GraphScoringPatternParameters", "DisplayOrder"):
        op.execute("""
            DROP INDEX IF EXISTS UX_GraphScoringPatternParameters_DisplayOrder
            ON dbo.GraphScoringPatternParameters;
        """)
        op.execute("""
            ALTER TABLE dbo.GraphScoringPatternParameters
            DROP CONSTRAINT IF EXISTS CK_GraphScoringPatternParameters_DisplayOrder;
        """)
        op.execute("""
            ALTER TABLE dbo.GraphScoringPatternParameters DROP COLUMN DisplayOrder;
        """)
