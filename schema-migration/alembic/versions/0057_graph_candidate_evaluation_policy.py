"""Add policy-owned Graph candidate-evaluation controls.

Revision ID: 0057
Revises: 0056

The upgrade is intentionally resumable. SQL Server normally rolls Alembic's
transaction back after a failure, but each step also tolerates a column left
behind by an interrupted deployment.
"""

import json

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


def _column_is_nullable(table: str, column: str) -> bool:
    conn = op.get_bind()
    value = conn.execute(sa.text("""
        SELECT is_nullable
        FROM sys.columns
        WHERE object_id = OBJECT_ID(:table) AND name = :column
    """), {"table": f"dbo.{table}", "column": column}).scalar_one()
    return bool(value)


def _constraint_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(sa.text("""
        SELECT 1 FROM sys.check_constraints WHERE name = :name
    """), {"name": name}).scalar())


def upgrade():
    if not _column_exists("GraphScoringPatternParameters", "CandidateEvaluationJson"):
        op.execute("""
            ALTER TABLE dbo.GraphScoringPatternParameters
            ADD CandidateEvaluationJson NVARCHAR(MAX) NULL
        """)

    # Bind JSON as data. A JSON literal inside sa.text is unsafe because its
    # colons are interpreted as SQLAlchemy bind-parameter markers even though
    # they appear between SQL string quotes.
    conn = op.get_bind()
    ring_policy = json.dumps({
        "max_states": 100_000,
        "max_ring_size": 8,
        "limit_strategy": "BEST_EVIDENCE",
    }, separators=(",", ":"))
    conn.execute(sa.text("""
        UPDATE dbo.GraphScoringPatternParameters
        SET CandidateEvaluationJson = CASE
            WHEN PatternType = 'Ring' THEN :ring_policy
            ELSE :empty_policy
        END
        WHERE CandidateEvaluationJson IS NULL
    """), {"ring_policy": ring_policy, "empty_policy": "{}"})

    if _column_is_nullable(
        "GraphScoringPatternParameters", "CandidateEvaluationJson"
    ):
        op.execute("""
            ALTER TABLE dbo.GraphScoringPatternParameters
            ALTER COLUMN CandidateEvaluationJson NVARCHAR(MAX) NOT NULL
        """)

    constraint = "CK_GraphScoringPatternParameters_CandidateEvaluation"
    if not _constraint_exists(constraint):
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
