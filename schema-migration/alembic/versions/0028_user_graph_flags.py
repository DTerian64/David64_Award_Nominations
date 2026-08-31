"""Add dbo.UserGraphFlags and dbo.ApproverPairFlags

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-27

Context
-------
These two denormalised snapshot tables let the Random Forest read pre-computed
graph signals at both training time and inference time without scanning the JSON
columns of dbo.GraphPatternFindings.

dbo.UserGraphFlags — one row per (TenantId, UserId, AsOfDate).
  Populated by modeling/graph_analytics.py after each weekly run. The batch job
  APPENDS a new row rather than overwriting, producing a time-series of snapshots.

  At training time, modeling/train_rf_model.py uses a point-in-time OUTER APPLY to
  join the closest snapshot whose AsOfDate is ≤ each nomination's NominationDate,
  eliminating data leakage from future graph findings.

  At inference time, inference/random_forest_check.py reads the row with the latest AsOfDate for
  each user — a simple keyed lookup on the primary key index.

dbo.ApproverPairFlags — one row per (TenantId, ApproverId, NominatorId,
  BeneficiaryId, AsOfDate).
  Populated from Approved/Paid nomination history each weekly run.  Used by the
  Approver RF to detect approver-pair collusion (same approver always approves
  nominations from A to B).

Downgrade
---------
Drops both tables.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = :t AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name},
    )
    return result.fetchone() is not None


def _index_exists(conn, index_name: str, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM sys.indexes "
            "WHERE name = :n AND object_id = OBJECT_ID(:t)"
        ),
        {"n": index_name, "t": f"dbo.{table_name}"},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # ── dbo.UserGraphFlags ─────────────────────────────────────────────────────
    if not _table_exists(conn, "UserGraphFlags"):
        conn.execute(sa.text("""
            CREATE TABLE dbo.UserGraphFlags (
                TenantId                 INT           NOT NULL,
                UserId                   INT           NOT NULL,
                AsOfDate                 DATE          NOT NULL,

                IsInRing                 BIT           NOT NULL DEFAULT 0,
                RingMaxUserCount         TINYINT       NOT NULL DEFAULT 0,
                RingMaxNominationCount   TINYINT       NOT NULL DEFAULT 0,

                IsSuperNominator         BIT           NOT NULL DEFAULT 0,

                IsInCopyPasteCluster     BIT           NOT NULL DEFAULT 0,
                CopyPasteClusterSize     SMALLINT      NOT NULL DEFAULT 0,

                HasTransactionalLanguage BIT           NOT NULL DEFAULT 0,

                IsApproverAffinity       BIT           NOT NULL DEFAULT 0,

                HighestSeverity          VARCHAR(10)   NULL,

                LastUpdatedUtc           DATETIME2     NOT NULL
                    DEFAULT SYSUTCDATETIME(),

                CONSTRAINT PK_UserGraphFlags
                    PRIMARY KEY (TenantId, UserId, AsOfDate)
            )
        """))

    if not _index_exists(conn, "IX_UserGraphFlags_PointInTime", "UserGraphFlags"):
        conn.execute(sa.text("""
            CREATE NONCLUSTERED INDEX IX_UserGraphFlags_PointInTime
                ON dbo.UserGraphFlags (TenantId, UserId, AsOfDate DESC)
        """))

    # ── dbo.ApproverPairFlags ──────────────────────────────────────────────────
    if not _table_exists(conn, "ApproverPairFlags"):
        conn.execute(sa.text("""
            CREATE TABLE dbo.ApproverPairFlags (
                TenantId          INT           NOT NULL,
                ApproverId        INT           NOT NULL,
                NominatorId       INT           NOT NULL,
                BeneficiaryId     INT           NOT NULL,
                AsOfDate          DATE          NOT NULL,

                PairApprovalCount SMALLINT      NOT NULL DEFAULT 0,

                LastUpdatedUtc    DATETIME2     NOT NULL
                    DEFAULT SYSUTCDATETIME(),

                CONSTRAINT PK_ApproverPairFlags
                    PRIMARY KEY (TenantId, ApproverId, NominatorId,
                                 BeneficiaryId, AsOfDate)
            )
        """))

    if not _index_exists(conn, "IX_ApproverPairFlags_PointInTime", "ApproverPairFlags"):
        conn.execute(sa.text("""
            CREATE NONCLUSTERED INDEX IX_ApproverPairFlags_PointInTime
                ON dbo.ApproverPairFlags
                   (TenantId, ApproverId, NominatorId, BeneficiaryId, AsOfDate DESC)
        """))


def downgrade() -> None:
    conn = op.get_bind()

    if _index_exists(conn, "IX_ApproverPairFlags_PointInTime", "ApproverPairFlags"):
        conn.execute(sa.text(
            "DROP INDEX IX_ApproverPairFlags_PointInTime ON dbo.ApproverPairFlags"
        ))
    if _table_exists(conn, "ApproverPairFlags"):
        conn.execute(sa.text("DROP TABLE dbo.ApproverPairFlags"))

    if _index_exists(conn, "IX_UserGraphFlags_PointInTime", "UserGraphFlags"):
        conn.execute(sa.text(
            "DROP INDEX IX_UserGraphFlags_PointInTime ON dbo.UserGraphFlags"
        ))
    if _table_exists(conn, "UserGraphFlags"):
        conn.execute(sa.text("DROP TABLE dbo.UserGraphFlags"))
