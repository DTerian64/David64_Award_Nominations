"""Add policy-owned Graph candidate-evaluation controls.

Revision ID: 0057
Revises: 0056
"""

import sqlalchemy as sa
from alembic import op


revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(sa.text("""
        SELECT 1
        FROM sys.columns
        WHERE object_id = OBJECT_ID(:table) AND name = :column
    """), {"table": f"dbo.{table}", "column": column}).scalar())


def upgrade():
    if not _column_exists("GraphScoringPatternParameters", "CandidateEvaluationJson"):
        op.execute("""
            ALTER TABLE dbo.GraphScoringPatternParameters
            ADD CandidateEvaluationJson NVARCHAR(MAX) NULL
        """)
        op.execute("""
            UPDATE dbo.GraphScoringPatternParameters
            SET CandidateEvaluationJson = CASE
                WHEN PatternType = 'Ring' THEN
                    '{"max_states":100000,"max_ring_size":8,"limit_strategy":"BEST_EVIDENCE"}'
                ELSE '{}'
            END
        """)
        op.execute("""
            ALTER TABLE dbo.GraphScoringPatternParameters
            ALTER COLUMN CandidateEvaluationJson NVARCHAR(MAX) NOT NULL
        """)
        op.execute("""
            ALTER TABLE dbo.GraphScoringPatternParameters
            ADD CONSTRAINT CK_GraphScoringPatternParameters_CandidateEvaluation
            CHECK (ISJSON(CandidateEvaluationJson) = 1)
        """)


def downgrade():
    if _column_exists("GraphScoringPatternParameters", "CandidateEvaluationJson"):
        op.execute("""
            ALTER TABLE dbo.GraphScoringPatternParameters
            DROP CONSTRAINT IF EXISTS CK_GraphScoringPatternParameters_CandidateEvaluation
        """)
        op.execute("""
            ALTER TABLE dbo.GraphScoringPatternParameters
            DROP COLUMN CandidateEvaluationJson
        """)
