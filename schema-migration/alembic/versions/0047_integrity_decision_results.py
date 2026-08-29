"""Create the four-engine IntegrityDecisionResults compatibility table.

Revision ID: 0047
Revises: 0046
Create Date: 2026-08-28

This revision creates the new decision contract only. It deliberately does not
backfill historical data from FraudDecisionResults or HRBP_FraudFlags. New
integrity checks dual-write both generations while readers retain a legacy
fallback. Historical migration belongs in a later, independently deployable
revision after the JSON contracts have been validated.
"""

import sqlalchemy as sa
from alembic import op


revision = "0047"
down_revision = "0046"
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


def _constraint_exists(table: str, constraint: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM sys.check_constraints "
            "WHERE parent_object_id = OBJECT_ID(:table) AND name = :constraint"
        ),
        {"table": f"dbo.{table}", "constraint": constraint},
    ).fetchone() is not None


def _drop_constraint(table: str, constraint: str) -> None:
    if _constraint_exists(table, constraint):
        op.execute(f"ALTER TABLE dbo.{table} DROP CONSTRAINT {constraint};")


def _extend_legacy_adjudication_constraints() -> None:
    """Allow semantic confirmations while FraudDecisionResults is dual-written."""
    if not _table_exists("FraudDecisionResults"):
        return

    _drop_constraint(
        "FraudDecisionResults", "CK_FraudDecisionResults_HumanTrainingPair"
    )
    _drop_constraint(
        "FraudDecisionResults", "CK_FraudDecisionResults_HumanReviewOutcome"
    )
    op.execute("""
        ALTER TABLE dbo.FraudDecisionResults
        ADD CONSTRAINT CK_FraudDecisionResults_HumanReviewOutcome
        CHECK (HumanReviewOutcome IS NULL OR HumanReviewOutcome IN (
            'CONFIRMED_CONCERN',
            'CONFIRMED_SEMANTIC_CONCERN',
            'CLEARED_NO_CONCERN',
            'CLEARED_UNSUBSTANTIATED'
        ));
    """)
    op.execute("""
        ALTER TABLE dbo.FraudDecisionResults
        ADD CONSTRAINT CK_FraudDecisionResults_HumanTrainingPair
        CHECK (
            (HumanReviewOutcome IS NULL AND TrainingDisposition IS NULL)
            OR (HumanReviewOutcome = 'CONFIRMED_CONCERN'
                AND TrainingDisposition = 'FRAUD')
            OR (HumanReviewOutcome = 'CLEARED_NO_CONCERN'
                AND TrainingDisposition = 'LEGITIMATE')
            OR (HumanReviewOutcome IN (
                    'CLEARED_UNSUBSTANTIATED',
                    'CONFIRMED_SEMANTIC_CONCERN'
                ) AND TrainingDisposition = 'EXCLUDED')
        );
    """)


def upgrade() -> None:
    _extend_legacy_adjudication_constraints()

    if _table_exists("IntegrityDecisionResults"):
        return

    op.execute("""
        CREATE TABLE dbo.IntegrityDecisionResults (
            NominationId             INT            NOT NULL,
            DecisionSchemaVersion    SMALLINT       NOT NULL,
            PolicyVersion            VARCHAR(40)    NOT NULL,
            SourceMessageId          NVARCHAR(128)  NULL,

            RfResultJson             NVARCHAR(MAX)  NOT NULL,
            GraphResultJson          NVARCHAR(MAX)  NOT NULL,
            GnnResultJson            NVARCHAR(MAX)  NOT NULL,
            SemanticResultJson       NVARCHAR(MAX)  NOT NULL,

            CompositeScore           INT            NULL,
            CompositeRiskLevel       VARCHAR(20)    NOT NULL,
            DecisiveEnginesJson      NVARCHAR(400)  NOT NULL,
            FinalRoute               VARCHAR(40)    NOT NULL,
            RoutingRule              VARCHAR(100)   NOT NULL,
            ReviewScope              VARCHAR(30)    NULL,

            HumanReviewOutcome       VARCHAR(40)    NULL,
            TrainingDisposition      VARCHAR(20)    NULL,
            ReviewReason             NVARCHAR(2000) NULL,
            ReviewedBy               NVARCHAR(256)  NULL,
            ReviewedAt               DATETIME2(7)   NULL,

            ScoredBy                 NVARCHAR(256)  NOT NULL,
            CreatedAt                DATETIME2(7)   NOT NULL
                CONSTRAINT DF_IntegrityDecisionResults_CreatedAt
                DEFAULT SYSUTCDATETIME(),
            UpdatedAt                DATETIME2(7)   NOT NULL
                CONSTRAINT DF_IntegrityDecisionResults_UpdatedAt
                DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_IntegrityDecisionResults
                PRIMARY KEY CLUSTERED (NominationId),
            CONSTRAINT FK_IntegrityDecisionResults_Nominations
                FOREIGN KEY (NominationId)
                REFERENCES dbo.Nominations (NominationId),
            CONSTRAINT CK_IntegrityDecisionResults_SchemaVersion
                CHECK (DecisionSchemaVersion >= 2),
            CONSTRAINT CK_IntegrityDecisionResults_RfJson
                CHECK (ISJSON(RfResultJson) = 1),
            CONSTRAINT CK_IntegrityDecisionResults_GraphJson
                CHECK (ISJSON(GraphResultJson) = 1),
            CONSTRAINT CK_IntegrityDecisionResults_GnnJson
                CHECK (ISJSON(GnnResultJson) = 1),
            CONSTRAINT CK_IntegrityDecisionResults_SemanticJson
                CHECK (ISJSON(SemanticResultJson) = 1),
            CONSTRAINT CK_IntegrityDecisionResults_DecisiveEnginesJson
                CHECK (ISJSON(DecisiveEnginesJson) = 1),
            CONSTRAINT CK_IntegrityDecisionResults_CompositeScore
                CHECK (CompositeScore IS NULL OR CompositeScore BETWEEN 0 AND 100),
            CONSTRAINT CK_IntegrityDecisionResults_CompositeRisk
                CHECK (CompositeRiskLevel IN (
                    'UNKNOWN', 'NONE', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
                )),
            CONSTRAINT CK_IntegrityDecisionResults_FinalRoute
                CHECK (FinalRoute IN (
                    'MANAGER_APPROVAL', 'HRBP_REVIEW', 'REJECT_SEMANTIC'
                )),
            CONSTRAINT CK_IntegrityDecisionResults_ReviewScope
                CHECK (ReviewScope IS NULL OR ReviewScope IN (
                    'FRAUD', 'SEMANTIC', 'FRAUD_AND_SEMANTIC'
                )),
            CONSTRAINT CK_IntegrityDecisionResults_RouteScope
                CHECK (
                    (FinalRoute = 'HRBP_REVIEW' AND ReviewScope IS NOT NULL)
                    OR (FinalRoute <> 'HRBP_REVIEW' AND ReviewScope IS NULL)
                ),
            CONSTRAINT CK_IntegrityDecisionResults_HumanOutcome
                CHECK (HumanReviewOutcome IS NULL OR HumanReviewOutcome IN (
                    'CONFIRMED_CONCERN',
                    'CONFIRMED_SEMANTIC_CONCERN',
                    'CLEARED_NO_CONCERN',
                    'CLEARED_UNSUBSTANTIATED'
                )),
            CONSTRAINT CK_IntegrityDecisionResults_TrainingDisposition
                CHECK (TrainingDisposition IS NULL OR TrainingDisposition IN (
                    'FRAUD', 'LEGITIMATE', 'EXCLUDED'
                )),
            CONSTRAINT CK_IntegrityDecisionResults_HumanTrainingPair
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
                ),
            CONSTRAINT CK_IntegrityDecisionResults_OutcomeScope
                CHECK (
                    HumanReviewOutcome IS NULL
                    OR (ReviewScope IN ('FRAUD', 'FRAUD_AND_SEMANTIC')
                        AND HumanReviewOutcome IN (
                            'CONFIRMED_CONCERN',
                            'CLEARED_NO_CONCERN',
                            'CLEARED_UNSUBSTANTIATED'
                        ))
                    OR (ReviewScope IN ('SEMANTIC', 'FRAUD_AND_SEMANTIC')
                        AND HumanReviewOutcome IN (
                            'CONFIRMED_SEMANTIC_CONCERN',
                            'CLEARED_UNSUBSTANTIATED'
                        ))
                )
        );
    """)

    op.execute("""
        CREATE INDEX IX_IntegrityDecisionResults_RouteRisk
        ON dbo.IntegrityDecisionResults (
            FinalRoute, CompositeRiskLevel, CreatedAt DESC
        )
        INCLUDE (NominationId, CompositeScore, ReviewScope);
    """)
    op.execute("""
        CREATE INDEX IX_IntegrityDecisionResults_HRBPQueue
        ON dbo.IntegrityDecisionResults (ReviewScope, CreatedAt ASC)
        INCLUDE (NominationId, CompositeScore, CompositeRiskLevel)
        WHERE FinalRoute = 'HRBP_REVIEW';
    """)
    op.execute("""
        CREATE INDEX IX_IntegrityDecisionResults_TrainingDisposition
        ON dbo.IntegrityDecisionResults (TrainingDisposition, ReviewedAt DESC)
        INCLUDE (NominationId, HumanReviewOutcome, ReviewedBy)
        WHERE TrainingDisposition IS NOT NULL;
    """)


def downgrade() -> None:
    if _table_exists("IntegrityDecisionResults"):
        op.execute("DROP TABLE dbo.IntegrityDecisionResults;")

    if not _table_exists("FraudDecisionResults"):
        return

    # The v1 schema cannot represent a semantic confirmation. Preserve the
    # explicit no-training treatment while degrading the workflow label.
    op.execute("""
        UPDATE dbo.FraudDecisionResults
        SET HumanReviewOutcome = 'CLEARED_UNSUBSTANTIATED',
            TrainingDisposition = 'EXCLUDED',
            UpdatedAt = SYSUTCDATETIME()
        WHERE HumanReviewOutcome = 'CONFIRMED_SEMANTIC_CONCERN';
    """)
    _drop_constraint(
        "FraudDecisionResults", "CK_FraudDecisionResults_HumanTrainingPair"
    )
    _drop_constraint(
        "FraudDecisionResults", "CK_FraudDecisionResults_HumanReviewOutcome"
    )
    op.execute("""
        ALTER TABLE dbo.FraudDecisionResults
        ADD CONSTRAINT CK_FraudDecisionResults_HumanReviewOutcome
        CHECK (HumanReviewOutcome IS NULL OR HumanReviewOutcome IN (
            'CONFIRMED_CONCERN',
            'CLEARED_NO_CONCERN',
            'CLEARED_UNSUBSTANTIATED'
        ));
    """)
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
