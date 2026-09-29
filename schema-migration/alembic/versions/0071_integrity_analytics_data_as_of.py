"""Add one immutable data cutoff to each integrity analytics execution.

Revision ID: 0071
Revises: 0070
Create Date: 2026-09-29

The database assigns the cutoff when the shared execution row is inserted.
Every replica reads that same value through idempotent execution registration,
so rolling-window analytics do not drift with worker start time or retries.
"""

from __future__ import annotations

from alembic import op


revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        IF COL_LENGTH(
            'ops.IntegrityAnalyticsJobRuns', 'DataAsOfUtc'
        ) IS NULL
        BEGIN
            ALTER TABLE ops.IntegrityAnalyticsJobRuns
                ADD DataAsOfUtc DATETIME2(3) NULL;

            UPDATE ops.IntegrityAnalyticsJobRuns
            SET DataAsOfUtc = StartedAt
            WHERE DataAsOfUtc IS NULL;

            ALTER TABLE ops.IntegrityAnalyticsJobRuns
                ALTER COLUMN DataAsOfUtc DATETIME2(3) NOT NULL;
        END;

        IF NOT EXISTS (
            SELECT 1
            FROM sys.default_constraints AS dc
            JOIN sys.columns AS c
              ON c.object_id = dc.parent_object_id
             AND c.column_id = dc.parent_column_id
            WHERE dc.parent_object_id = OBJECT_ID(
                N'ops.IntegrityAnalyticsJobRuns'
            )
              AND c.name = N'DataAsOfUtc'
        )
        BEGIN
            ALTER TABLE ops.IntegrityAnalyticsJobRuns
                ADD CONSTRAINT DF_IntegrityAnalyticsJobRuns_DataAsOfUtc
                DEFAULT SYSUTCDATETIME() FOR DataAsOfUtc;
        END;
    """)


def downgrade() -> None:
    op.execute("""
        IF EXISTS (
            SELECT 1
            FROM sys.default_constraints
            WHERE parent_object_id = OBJECT_ID(
                N'ops.IntegrityAnalyticsJobRuns'
            )
              AND name = N'DF_IntegrityAnalyticsJobRuns_DataAsOfUtc'
        )
        BEGIN
            ALTER TABLE ops.IntegrityAnalyticsJobRuns
                DROP CONSTRAINT DF_IntegrityAnalyticsJobRuns_DataAsOfUtc;
        END;

        IF COL_LENGTH(
            'ops.IntegrityAnalyticsJobRuns', 'DataAsOfUtc'
        ) IS NOT NULL
        BEGIN
            ALTER TABLE ops.IntegrityAnalyticsJobRuns
                DROP COLUMN DataAsOfUtc;
        END;
    """)
