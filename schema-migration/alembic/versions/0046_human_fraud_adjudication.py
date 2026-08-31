"""Add model-neutral human adjudication to FraudDecisionResults.

Revision ID: 0046
Revises: 0045
Create Date: 2026-08-26

The three component scores are immutable inference evidence. HRBP outcomes are
stored beside that evidence rather than overwriting the RF-owned
P2P_FraudScores row. TrainingDisposition explicitly distinguishes a confirmed
fraud label, a confirmed legitimate label, and a reviewed outcome that must be
excluded from model training.
"""

import sqlalchemy as sa
from alembic import op


revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :name"
        ),
        {"name": name},
    ).fetchone() is not None


def _column_exists(table: str, column: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :table "
            "AND COLUMN_NAME = :column"
        ),
        {"table": table, "column": column},
    ).fetchone() is not None


def _constraint_exists(table: str, constraint: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM sys.check_constraints "
            "WHERE parent_object_id = OBJECT_ID(:table) AND name = :constraint"
        ),
        {"table": f"dbo.{table}", "constraint": constraint},
    ).fetchone() is not None


def _index_exists(table: str, index: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM sys.indexes "
            "WHERE object_id = OBJECT_ID(:table) AND name = :index"
        ),
        {"table": f"dbo.{table}", "index": index},
    ).fetchone() is not None


_COLUMNS = (
    ("HumanReviewOutcome", "VARCHAR(40) NULL"),
    ("TrainingDisposition", "VARCHAR(20) NULL"),
    ("ReviewReason", "NVARCHAR(2000) NULL"),
    ("ReviewedBy", "NVARCHAR(256) NULL"),
    ("ReviewedAt", "DATETIME2 NULL"),
)


def upgrade() -> None:
    if not _table_exists("FraudDecisionResults"):
        return

    for column, sql_type in _COLUMNS:
        if not _column_exists("FraudDecisionResults", column):
            op.execute(
                f"ALTER TABLE dbo.FraudDecisionResults ADD {column} {sql_type};"
            )

    if not _constraint_exists(
        "FraudDecisionResults", "CK_FraudDecisionResults_HumanReviewOutcome"
    ):
        op.execute("""
            ALTER TABLE dbo.FraudDecisionResults
            ADD CONSTRAINT CK_FraudDecisionResults_HumanReviewOutcome
            CHECK (HumanReviewOutcome IS NULL OR HumanReviewOutcome IN (
                'CONFIRMED_CONCERN',
                'CLEARED_NO_CONCERN',
                'CLEARED_UNSUBSTANTIATED'
            ));
        """)

    if not _constraint_exists(
        "FraudDecisionResults", "CK_FraudDecisionResults_TrainingDisposition"
    ):
        op.execute("""
            ALTER TABLE dbo.FraudDecisionResults
            ADD CONSTRAINT CK_FraudDecisionResults_TrainingDisposition
            CHECK (TrainingDisposition IS NULL OR TrainingDisposition IN (
                'FRAUD', 'LEGITIMATE', 'EXCLUDED'
            ));
        """)

    if not _constraint_exists(
        "FraudDecisionResults", "CK_FraudDecisionResults_HumanTrainingPair"
    ):
        op.execute("""
            ALTER TABLE dbo.FraudDecisionResults
            ADD CONSTRAINT CK_FraudDecisionResults_HumanTrainingPair
            CHECK (
                (HumanReviewOutcome IS NULL AND TrainingDisposition IS NULL)
                OR (HumanReviewOutcome = 'CONFIRMED_CONCERN'
                    AND TrainingDisposition = 'FRAUD')
                OR (HumanReviewOutcome = 'CLEARED_NO_CONCERN'
                    AND TrainingDisposition = 'LEGITIMATE')
                OR (HumanReviewOutcome = 'CLEARED_UNSUBSTANTIATED'
                    AND TrainingDisposition = 'EXCLUDED')
            );
        """)

    if not _index_exists(
        "FraudDecisionResults", "IX_FraudDecisionResults_TrainingDisposition"
    ):
        op.execute("""
            CREATE INDEX IX_FraudDecisionResults_TrainingDisposition
            ON dbo.FraudDecisionResults (TrainingDisposition, ReviewedAt DESC)
            INCLUDE (NominationId, HumanReviewOutcome, ReviewedBy);
        """)

    # Preserve any human confirmations written by the legacy RF-owned path.
    if _table_exists("P2P_FraudScores"):
        op.execute("""
            UPDATE fdr
            SET HumanReviewOutcome = CASE
                    WHEN p2p.IsFraud = 1 THEN 'CONFIRMED_CONCERN'
                    ELSE 'CLEARED_NO_CONCERN'
                END,
                TrainingDisposition = CASE
                    WHEN p2p.IsFraud = 1 THEN 'FRAUD'
                    ELSE 'LEGITIMATE'
                END,
                ReviewReason = COALESCE(
                    n.RejectionReason,
                    'Migrated from legacy P2P human confirmation'
                ),
                ReviewedBy = p2p.ConfirmedBy,
                ReviewedAt = p2p.ConfirmedAt,
                UpdatedAt = SYSUTCDATETIME()
            FROM dbo.FraudDecisionResults fdr
            JOIN dbo.P2P_FraudScores p2p
              ON p2p.NominationId = fdr.NominationId
            JOIN dbo.Nominations n
              ON n.NominationId = fdr.NominationId
            WHERE p2p.ConfirmedBy IS NOT NULL
              AND p2p.IsFraud IS NOT NULL
              AND fdr.HumanReviewOutcome IS NULL;
        """)


def downgrade() -> None:
    if not _table_exists("FraudDecisionResults"):
        return

    if _index_exists(
        "FraudDecisionResults", "IX_FraudDecisionResults_TrainingDisposition"
    ):
        op.execute(
            "DROP INDEX IX_FraudDecisionResults_TrainingDisposition "
            "ON dbo.FraudDecisionResults;"
        )

    for constraint in (
        "CK_FraudDecisionResults_HumanTrainingPair",
        "CK_FraudDecisionResults_TrainingDisposition",
        "CK_FraudDecisionResults_HumanReviewOutcome",
    ):
        if _constraint_exists("FraudDecisionResults", constraint):
            op.execute(
                f"ALTER TABLE dbo.FraudDecisionResults DROP CONSTRAINT {constraint};"
            )

    for column, _ in reversed(_COLUMNS):
        if _column_exists("FraudDecisionResults", column):
            op.execute(f"ALTER TABLE dbo.FraudDecisionResults DROP COLUMN {column};")
