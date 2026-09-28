"""Create the integrity analytics coordination and execution-history schema.

Revision ID: 0070
Revises: 0069
Create Date: 2026-09-28

The tables in ``ops`` are both the coordination store for parallel Container
Apps Job replicas and the durable operational history for each execution.
Coordination state is relational; JSON is restricted to bounded diagnostics.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'ops' AND TABLE_NAME = :name"
        ),
        {"name": name},
    ).fetchone() is not None


def upgrade() -> None:
    op.execute("""
        IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'ops')
            EXEC(N'CREATE SCHEMA ops AUTHORIZATION dbo');
    """)

    if not _table_exists("IntegrityAnalyticsJobRuns"):
        op.execute("""
            CREATE TABLE ops.IntegrityAnalyticsJobRuns (
                RunId                       UNIQUEIDENTIFIER NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsJobRuns_RunId
                    DEFAULT NEWSEQUENTIALID(),
                ExecutionName               NVARCHAR(256) NOT NULL,
                JobName                     NVARCHAR(256) NOT NULL,
                PreparationStatus           VARCHAR(16) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsJobRuns_PreparationStatus
                    DEFAULT 'PENDING',
                PreparationWorkerId         UNIQUEIDENTIFIER NULL,
                PreparationLeaseExpiresAt   DATETIME2(3) NULL,
                PreparationLastHeartbeatAt  DATETIME2(3) NULL,
                FinalizationStatus          VARCHAR(16) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsJobRuns_FinalizationStatus
                    DEFAULT 'PENDING',
                FinalizationWorkerId        UNIQUEIDENTIFIER NULL,
                FinalizationLeaseExpiresAt  DATETIME2(3) NULL,
                FinalizationLastHeartbeatAt DATETIME2(3) NULL,
                ResultStatus                VARCHAR(16) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsJobRuns_ResultStatus
                    DEFAULT 'RUNNING',
                StartedAt                   DATETIME2(3) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsJobRuns_StartedAt
                    DEFAULT SYSUTCDATETIME(),
                CompletedAt                 DATETIME2(3) NULL,
                FailureDetail               NVARCHAR(2000) NULL,
                SummaryJson                 NVARCHAR(4000) NULL,
                created_at                  DATETIME2(3) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsJobRuns_created_at
                    DEFAULT SYSUTCDATETIME(),
                created_by                  NVARCHAR(256) NOT NULL,
                updated_at                  DATETIME2(3) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsJobRuns_updated_at
                    DEFAULT SYSUTCDATETIME(),
                updated_by                  NVARCHAR(256) NOT NULL,
                CONSTRAINT PK_IntegrityAnalyticsJobRuns
                    PRIMARY KEY CLUSTERED (RunId),
                CONSTRAINT UQ_IntegrityAnalyticsJobRuns_ExecutionName
                    UNIQUE (ExecutionName),
                CONSTRAINT CK_IntegrityAnalyticsJobRuns_PreparationStatus
                    CHECK (PreparationStatus IN ('PENDING','RUNNING','READY','FAILED')),
                CONSTRAINT CK_IntegrityAnalyticsJobRuns_FinalizationStatus
                    CHECK (FinalizationStatus IN ('PENDING','RUNNING','COMPLETE')),
                CONSTRAINT CK_IntegrityAnalyticsJobRuns_ResultStatus
                    CHECK (ResultStatus IN ('RUNNING','SUCCEEDED','FAILED')),
                CONSTRAINT CK_IntegrityAnalyticsJobRuns_SummaryJson
                    CHECK (SummaryJson IS NULL OR ISJSON(SummaryJson) = 1)
            );
        """)
        op.execute("""
            CREATE INDEX IX_IntegrityAnalyticsJobRuns_Result_Started
                ON ops.IntegrityAnalyticsJobRuns (ResultStatus, StartedAt DESC);
        """)

    if not _table_exists("IntegrityAnalyticsTenantRuns"):
        op.execute("""
            CREATE TABLE ops.IntegrityAnalyticsTenantRuns (
                RunId             UNIQUEIDENTIFIER NOT NULL,
                TenantId          INT NOT NULL,
                Status            VARCHAR(32) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsTenantRuns_Status
                    DEFAULT 'PENDING',
                WorkerId          UNIQUEIDENTIFIER NULL,
                LeaseExpiresAt    DATETIME2(3) NULL,
                LastHeartbeatAt   DATETIME2(3) NULL,
                AttemptCount      INT NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsTenantRuns_AttemptCount
                    DEFAULT 0,
                CurrentStage      VARCHAR(16) NULL,
                StartedAt         DATETIME2(3) NULL,
                CompletedAt       DATETIME2(3) NULL,
                FailureDetail     NVARCHAR(2000) NULL,
                created_at        DATETIME2(3) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsTenantRuns_created_at
                    DEFAULT SYSUTCDATETIME(),
                created_by        NVARCHAR(256) NOT NULL,
                updated_at        DATETIME2(3) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsTenantRuns_updated_at
                    DEFAULT SYSUTCDATETIME(),
                updated_by        NVARCHAR(256) NOT NULL,
                CONSTRAINT PK_IntegrityAnalyticsTenantRuns
                    PRIMARY KEY CLUSTERED (RunId, TenantId),
                CONSTRAINT FK_IntegrityAnalyticsTenantRuns_JobRuns
                    FOREIGN KEY (RunId)
                    REFERENCES ops.IntegrityAnalyticsJobRuns (RunId)
                    ON DELETE CASCADE,
                CONSTRAINT FK_IntegrityAnalyticsTenantRuns_Tenants
                    FOREIGN KEY (TenantId) REFERENCES dbo.Tenants (TenantId),
                CONSTRAINT CK_IntegrityAnalyticsTenantRuns_Status
                    CHECK (Status IN (
                        'PENDING','RUNNING','SUCCEEDED','FAILED',
                        'SKIPPED_DISABLED','SKIPPED_NO_WORK'
                    )),
                CONSTRAINT CK_IntegrityAnalyticsTenantRuns_AttemptCount
                    CHECK (AttemptCount >= 0),
                CONSTRAINT CK_IntegrityAnalyticsTenantRuns_CurrentStage
                    CHECK (CurrentStage IS NULL OR CurrentStage IN (
                        'GRAPH','TABULAR','GNN','FORECAST'
                    ))
            );
        """)
        op.execute("""
            CREATE INDEX IX_IntegrityAnalyticsTenantRuns_Claim
                ON ops.IntegrityAnalyticsTenantRuns
                    (RunId, Status, LeaseExpiresAt, TenantId)
                INCLUDE (AttemptCount, WorkerId);
        """)
        op.execute("""
            CREATE INDEX IX_IntegrityAnalyticsTenantRuns_Worker
                ON ops.IntegrityAnalyticsTenantRuns
                    (RunId, WorkerId, Status);
        """)

    if not _table_exists("IntegrityAnalyticsStageAttempts"):
        op.execute("""
            CREATE TABLE ops.IntegrityAnalyticsStageAttempts (
                StageAttemptId BIGINT IDENTITY(1,1) NOT NULL,
                RunId          UNIQUEIDENTIFIER NOT NULL,
                TenantId       INT NOT NULL,
                WorkerId       UNIQUEIDENTIFIER NOT NULL,
                Stage          VARCHAR(16) NOT NULL,
                AttemptNumber  INT NOT NULL,
                Status         VARCHAR(16) NOT NULL,
                StartedAt      DATETIME2(3) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsStageAttempts_StartedAt
                    DEFAULT SYSUTCDATETIME(),
                CompletedAt    DATETIME2(3) NULL,
                DurationSeconds DECIMAL(18,3) NULL,
                ReasonCode     VARCHAR(64) NULL,
                FailureDetail  NVARCHAR(2000) NULL,
                StageRunId     VARCHAR(128) NULL,
                PublishedVersion VARCHAR(128) NULL,
                DiagnosticsJson NVARCHAR(4000) NULL,
                created_at     DATETIME2(3) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsStageAttempts_created_at
                    DEFAULT SYSUTCDATETIME(),
                created_by     NVARCHAR(256) NOT NULL,
                updated_at     DATETIME2(3) NOT NULL
                    CONSTRAINT DF_IntegrityAnalyticsStageAttempts_updated_at
                    DEFAULT SYSUTCDATETIME(),
                updated_by     NVARCHAR(256) NOT NULL,
                CONSTRAINT PK_IntegrityAnalyticsStageAttempts
                    PRIMARY KEY CLUSTERED (StageAttemptId),
                CONSTRAINT FK_IntegrityAnalyticsStageAttempts_TenantRuns
                    FOREIGN KEY (RunId, TenantId)
                    REFERENCES ops.IntegrityAnalyticsTenantRuns (RunId, TenantId)
                    ON DELETE CASCADE,
                CONSTRAINT UQ_IntegrityAnalyticsStageAttempts_Attempt
                    UNIQUE (RunId, TenantId, Stage, AttemptNumber),
                CONSTRAINT CK_IntegrityAnalyticsStageAttempts_Stage
                    CHECK (Stage IN ('GRAPH','TABULAR','GNN','FORECAST')),
                CONSTRAINT CK_IntegrityAnalyticsStageAttempts_AttemptNumber
                    CHECK (AttemptNumber > 0),
                CONSTRAINT CK_IntegrityAnalyticsStageAttempts_Status
                    CHECK (Status IN (
                        'RUNNING','SUCCEEDED','FAILED','SKIPPED','ABANDONED'
                    )),
                CONSTRAINT CK_IntegrityAnalyticsStageAttempts_Duration
                    CHECK (DurationSeconds IS NULL OR DurationSeconds >= 0),
                CONSTRAINT CK_IntegrityAnalyticsStageAttempts_DiagnosticsJson
                    CHECK (DiagnosticsJson IS NULL OR ISJSON(DiagnosticsJson) = 1)
            );
        """)
        op.execute("""
            CREATE INDEX IX_IntegrityAnalyticsStageAttempts_Operations
                ON ops.IntegrityAnalyticsStageAttempts
                    (Stage, Status, StartedAt DESC)
                INCLUDE (RunId, TenantId, WorkerId, DurationSeconds, ReasonCode);
        """)


def downgrade() -> None:
    if _table_exists("IntegrityAnalyticsStageAttempts"):
        op.execute("DROP TABLE ops.IntegrityAnalyticsStageAttempts;")
    if _table_exists("IntegrityAnalyticsTenantRuns"):
        op.execute("DROP TABLE ops.IntegrityAnalyticsTenantRuns;")
    if _table_exists("IntegrityAnalyticsJobRuns"):
        op.execute("DROP TABLE ops.IntegrityAnalyticsJobRuns;")
    op.execute("""
        IF EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'ops')
           AND NOT EXISTS (
               SELECT 1 FROM sys.objects WHERE schema_id = SCHEMA_ID(N'ops')
           )
            EXEC(N'DROP SCHEMA ops');
    """)
