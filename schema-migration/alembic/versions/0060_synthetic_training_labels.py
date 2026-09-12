"""Add isolated synthetic-tenant training-label provenance.

Revision ID: 0060
Revises: 0059

Synthetic ground truth is allowed only for explicitly synthetic tenants.  The
database stores provenance beside the model-neutral training disposition while
keeping human-review fields null for imported synthetic history.  Existing
reviewed dispositions are backfilled as HUMAN_INVESTIGATION.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=:table AND COLUMN_NAME=:column"
        ),
        {"table": table, "column": column},
    ).fetchone() is not None


def _constraint_exists(table: str, constraint: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM sys.check_constraints "
            "WHERE parent_object_id=OBJECT_ID(:table) AND name=:constraint"
        ),
        {"table": f"dbo.{table}", "constraint": constraint},
    ).fetchone() is not None


def _index_exists(table: str, index: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM sys.indexes "
            "WHERE object_id=OBJECT_ID(:table) AND name=:index"
        ),
        {"table": f"dbo.{table}", "index": index},
    ).fetchone() is not None


def _drop_constraint(table: str, constraint: str) -> None:
    if _constraint_exists(table, constraint):
        op.execute(f"ALTER TABLE dbo.{table} DROP CONSTRAINT {constraint};")


def _add_original_human_pair_constraint() -> None:
    op.execute("""
        ALTER TABLE dbo.IntegrityDecisionResults
        ADD CONSTRAINT CK_IntegrityDecisionResults_HumanTrainingPair
        CHECK (
            (HumanReviewOutcome IS NULL
                AND TrainingDisposition IS NULL
                AND ReviewReason IS NULL
                AND ReviewedBy IS NULL
                AND ReviewedAt IS NULL)
            OR (HumanReviewOutcome = 'CONFIRMED_CONCERN'
                AND TrainingDisposition = 'FRAUD'
                AND ReviewReason IS NOT NULL
                AND ReviewedBy IS NOT NULL
                AND ReviewedAt IS NOT NULL)
            OR (HumanReviewOutcome = 'CLEARED_NO_CONCERN'
                AND TrainingDisposition = 'LEGITIMATE'
                AND ReviewReason IS NOT NULL
                AND ReviewedBy IS NOT NULL
                AND ReviewedAt IS NOT NULL)
            OR (HumanReviewOutcome IN (
                    'CLEARED_UNSUBSTANTIATED',
                    'CONFIRMED_SEMANTIC_CONCERN'
                )
                AND TrainingDisposition = 'EXCLUDED'
                AND ReviewReason IS NOT NULL
                AND ReviewedBy IS NOT NULL
                AND ReviewedAt IS NOT NULL)
        );
    """)


def upgrade() -> None:
    if not _column_exists("Tenants", "is_synthetic"):
        op.execute("""
            ALTER TABLE dbo.Tenants
            ADD is_synthetic BIT NOT NULL
                CONSTRAINT DF_Tenants_is_synthetic DEFAULT (0);
        """)

    if not _column_exists("IntegrityDecisionResults", "TrainingDispositionSource"):
        op.execute("""
            ALTER TABLE dbo.IntegrityDecisionResults
            ADD TrainingDispositionSource VARCHAR(40) NULL;
        """)
    if not _column_exists(
        "IntegrityDecisionResults", "TrainingDispositionMetadataJson"
    ):
        op.execute("""
            ALTER TABLE dbo.IntegrityDecisionResults
            ADD TrainingDispositionMetadataJson NVARCHAR(MAX) NULL;
        """)

    # All dispositions written before this migration came through the existing
    # human adjudication workflow.  Give them explicit provenance before the
    # strengthened pair constraint is installed.
    op.execute("""
        UPDATE dbo.IntegrityDecisionResults
        SET TrainingDispositionSource = 'HUMAN_INVESTIGATION'
        WHERE TrainingDisposition IS NOT NULL
          AND TrainingDispositionSource IS NULL;
    """)

    _drop_constraint(
        "IntegrityDecisionResults", "CK_IntegrityDecisionResults_HumanTrainingPair"
    )

    if not _constraint_exists(
        "IntegrityDecisionResults", "CK_IntegrityDecisionResults_TrainingSource"
    ):
        op.execute("""
            ALTER TABLE dbo.IntegrityDecisionResults
            ADD CONSTRAINT CK_IntegrityDecisionResults_TrainingSource
            CHECK (
                TrainingDispositionSource IS NULL
                OR TrainingDispositionSource IN (
                    'HUMAN_INVESTIGATION',
                    'RANDOM_AUDIT',
                    'SYNTHETIC_GROUND_TRUTH'
                )
            );
        """)

    if not _constraint_exists(
        "IntegrityDecisionResults", "CK_IntegrityDecisionResults_TrainingMetadataJson"
    ):
        op.execute("""
            ALTER TABLE dbo.IntegrityDecisionResults
            ADD CONSTRAINT CK_IntegrityDecisionResults_TrainingMetadataJson
            CHECK (
                TrainingDispositionMetadataJson IS NULL
                OR ISJSON(TrainingDispositionMetadataJson) = 1
            );
        """)

    op.execute("""
        ALTER TABLE dbo.IntegrityDecisionResults
        ADD CONSTRAINT CK_IntegrityDecisionResults_HumanTrainingPair
        CHECK (
            (
                HumanReviewOutcome IS NULL
                AND TrainingDisposition IS NULL
                AND TrainingDispositionSource IS NULL
                AND TrainingDispositionMetadataJson IS NULL
                AND ReviewReason IS NULL
                AND ReviewedBy IS NULL
                AND ReviewedAt IS NULL
            )
            OR (
                TrainingDispositionSource IN (
                    'HUMAN_INVESTIGATION', 'RANDOM_AUDIT'
                )
                AND (
                    (HumanReviewOutcome = 'CONFIRMED_CONCERN'
                        AND TrainingDisposition = 'FRAUD')
                    OR (HumanReviewOutcome = 'CLEARED_NO_CONCERN'
                        AND TrainingDisposition = 'LEGITIMATE')
                    OR (HumanReviewOutcome IN (
                            'CLEARED_UNSUBSTANTIATED',
                            'CONFIRMED_SEMANTIC_CONCERN'
                        )
                        AND TrainingDisposition = 'EXCLUDED')
                )
                AND ReviewReason IS NOT NULL
                AND ReviewedBy IS NOT NULL
                AND ReviewedAt IS NOT NULL
            )
            OR (
                HumanReviewOutcome IS NULL
                AND TrainingDisposition IN ('FRAUD', 'LEGITIMATE')
                AND TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH'
                AND TrainingDispositionMetadataJson IS NOT NULL
                AND ReviewReason IS NULL
                AND ReviewedBy IS NULL
                AND ReviewedAt IS NULL
            )
        );
    """)

    if not _index_exists(
        "IntegrityDecisionResults", "IX_IntegrityDecisionResults_TrainingSource"
    ):
        op.execute("""
            CREATE INDEX IX_IntegrityDecisionResults_TrainingSource
            ON dbo.IntegrityDecisionResults (
                TrainingDispositionSource, TrainingDisposition, CreatedAt DESC
            )
            INCLUDE (NominationId, TenantId)
            WHERE TrainingDispositionSource IS NOT NULL;
        """)


def downgrade() -> None:
    synthetic_count = int(op.get_bind().execute(sa.text("""
        SELECT COUNT(*)
        FROM dbo.IntegrityDecisionResults
        WHERE TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH';
    """)).scalar() or 0)
    if synthetic_count:
        raise RuntimeError(
            "Cannot downgrade 0060 while SYNTHETIC_GROUND_TRUTH rows exist; "
            "remove the isolated synthetic corpus first."
        )

    if _index_exists(
        "IntegrityDecisionResults", "IX_IntegrityDecisionResults_TrainingSource"
    ):
        op.execute(
            "DROP INDEX IX_IntegrityDecisionResults_TrainingSource "
            "ON dbo.IntegrityDecisionResults;"
        )

    _drop_constraint(
        "IntegrityDecisionResults", "CK_IntegrityDecisionResults_HumanTrainingPair"
    )
    _drop_constraint(
        "IntegrityDecisionResults", "CK_IntegrityDecisionResults_TrainingMetadataJson"
    )
    _drop_constraint(
        "IntegrityDecisionResults", "CK_IntegrityDecisionResults_TrainingSource"
    )

    if _column_exists(
        "IntegrityDecisionResults", "TrainingDispositionMetadataJson"
    ):
        op.execute("""
            ALTER TABLE dbo.IntegrityDecisionResults
            DROP COLUMN TrainingDispositionMetadataJson;
        """)
    if _column_exists("IntegrityDecisionResults", "TrainingDispositionSource"):
        op.execute("""
            ALTER TABLE dbo.IntegrityDecisionResults
            DROP COLUMN TrainingDispositionSource;
        """)

    _add_original_human_pair_constraint()

    if _column_exists("Tenants", "is_synthetic"):
        op.execute("""
            ALTER TABLE dbo.Tenants DROP CONSTRAINT DF_Tenants_is_synthetic;
        """)
        op.execute("ALTER TABLE dbo.Tenants DROP COLUMN is_synthetic;")
