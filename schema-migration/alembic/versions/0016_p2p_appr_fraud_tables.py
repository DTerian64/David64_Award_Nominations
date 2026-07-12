"""Split fraud scoring into P2P_FraudScores, Appr_FraudScores, HRBP_FraudFlags

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-30

Context
-------
Replaces the single dbo.FraudScores + dbo.FraudFlags tables with three
semantically distinct tables:

  dbo.P2P_FraudScores
      Peer-to-peer fraud score written by the backend at nomination
      submission time.  Uses features knowable at submission time only
      (nominator/beneficiary behaviour, amount, category).

  dbo.Appr_FraudScores
      Approver-behaviour fraud score written by the weekly batch job
      after nominations are Paid.  Uses post-decision features
      (HoursToApproval, HoursToPayment, IsRapidApproval, approver stats).

  dbo.HRBP_FraudFlags
      Full ML inference snapshot for nominations routed to HRBP review.
      FKs to P2P_FraudScores (HRBP routing is triggered by P2P score).
      Replaces dbo.FraudFlags.

Dropped tables
--------------
  dbo.FraudFlags   — replaced by dbo.HRBP_FraudFlags
  dbo.FraudScores  — replaced by dbo.P2P_FraudScores + dbo.Appr_FraudScores

Downgrade
---------
Drops the three new tables and recreates FraudScores + FraudFlags.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
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


# ── upgrade ───────────────────────────────────────────────────────────────────

def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. dbo.P2P_FraudScores ────────────────────────────────────────────────
    if not _table_exists(conn, "P2P_FraudScores"):
        op.create_table(
            "P2P_FraudScores",
            sa.Column("P2PScoreId",   sa.Integer(),  primary_key=True, autoincrement=True),
            sa.Column("NominationId", sa.Integer(),  sa.ForeignKey("Nominations.NominationId"), nullable=False),
            sa.Column("FraudScore",   sa.Integer(),  nullable=False),    # 0–100
            sa.Column("RiskLevel",    sa.String(20), nullable=False),    # NONE/LOW/MEDIUM/HIGH/CRITICAL/UNKNOWN
            sa.Column("FraudFlags",   sa.String(500), nullable=True),    # comma-separated warning flags
            sa.Column("CreatedAt",    sa.DateTime(), server_default=sa.text("GETUTCDATE()"), nullable=False),
            schema="dbo",
        )
        conn.execute(sa.text(
            "ALTER TABLE dbo.P2P_FraudScores "
            "ADD CONSTRAINT uq_p2p_fraudscores_nomination UNIQUE (NominationId)"
        ))
        conn.execute(sa.text(
            "CREATE INDEX ix_p2p_fraudscores_risklevel ON dbo.P2P_FraudScores (RiskLevel)"
        ))

    # ── 2. dbo.Appr_FraudScores ───────────────────────────────────────────────
    if not _table_exists(conn, "Appr_FraudScores"):
        op.create_table(
            "Appr_FraudScores",
            sa.Column("ApprScoreId",  sa.Integer(),  primary_key=True, autoincrement=True),
            sa.Column("NominationId", sa.Integer(),  sa.ForeignKey("Nominations.NominationId"), nullable=False),
            sa.Column("FraudScore",   sa.Integer(),  nullable=False),    # 0–100
            sa.Column("RiskLevel",    sa.String(20), nullable=False),    # NONE/LOW/MEDIUM/HIGH/CRITICAL/UNKNOWN
            sa.Column("FraudFlags",   sa.String(500), nullable=True),    # comma-separated warning flags
            sa.Column("CreatedAt",    sa.DateTime(), server_default=sa.text("GETUTCDATE()"), nullable=False),
            schema="dbo",
        )
        conn.execute(sa.text(
            "ALTER TABLE dbo.Appr_FraudScores "
            "ADD CONSTRAINT uq_appr_fraudscores_nomination UNIQUE (NominationId)"
        ))
        conn.execute(sa.text(
            "CREATE INDEX ix_appr_fraudscores_risklevel ON dbo.Appr_FraudScores (RiskLevel)"
        ))

    # ── 3. dbo.HRBP_FraudFlags ────────────────────────────────────────────────
    if not _table_exists(conn, "HRBP_FraudFlags"):
        op.create_table(
            "HRBP_FraudFlags",
            sa.Column("FlagId",             sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("NominationId",       sa.Integer(), sa.ForeignKey("Nominations.NominationId"), nullable=False),
            sa.Column("FraudScore",         sa.Integer(), nullable=False),
            sa.Column("FraudProbability",   sa.Float(),   nullable=False),
            sa.Column("RiskLevel",          sa.String(20), nullable=False),
            sa.Column("WarningFlags",       sa.String(500), nullable=True),
            sa.Column("TopFeaturesJson",    sa.Text(),    nullable=True),
            sa.Column("FeatureSummaryJson", sa.Text(),    nullable=True),
            sa.Column("CreatedAt",          sa.DateTime(), server_default=sa.text("GETUTCDATE()"), nullable=False),
            schema="dbo",
        )
        conn.execute(sa.text(
            "ALTER TABLE dbo.HRBP_FraudFlags "
            "ADD CONSTRAINT uq_hrbp_fraudflags_nomination UNIQUE (NominationId)"
        ))
        conn.execute(sa.text(
            "CREATE INDEX ix_hrbp_fraudflags_risklevel ON dbo.HRBP_FraudFlags (RiskLevel)"
        ))

    # ── 4. Drop old tables (FK-safe order: FraudFlags before FraudScores) ─────
    if _table_exists(conn, "FraudFlags"):
        op.drop_table("FraudFlags", schema="dbo")

    if _table_exists(conn, "FraudScores"):
        op.drop_table("FraudScores", schema="dbo")


# ── downgrade ──────────────────────────────────────────────────────────────────

def downgrade() -> None:
    conn = op.get_bind()

    # Recreate old tables
    if not _table_exists(conn, "FraudScores"):
        op.create_table(
            "FraudScores",
            sa.Column("FraudScoreId", sa.Integer(),  primary_key=True, autoincrement=True),
            sa.Column("NominationId", sa.Integer(),  sa.ForeignKey("Nominations.NominationId"), nullable=False),
            sa.Column("FraudScore",   sa.Integer(),  nullable=False),
            sa.Column("RiskLevel",    sa.String(50), nullable=False),
            sa.Column("FraudFlags",   sa.String(2000), nullable=True),
            schema="dbo",
        )

    if not _table_exists(conn, "FraudFlags"):
        op.create_table(
            "FraudFlags",
            sa.Column("FlagId",             sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("NominationId",       sa.Integer(), sa.ForeignKey("Nominations.NominationId"), nullable=False),
            sa.Column("FraudScore",         sa.Integer(), nullable=False),
            sa.Column("FraudProbability",   sa.Float(),   nullable=False),
            sa.Column("RiskLevel",          sa.String(20), nullable=False),
            sa.Column("WarningFlags",       sa.String(500), nullable=True),
            sa.Column("TopFeaturesJson",    sa.Text(),    nullable=True),
            sa.Column("FeatureSummaryJson", sa.Text(),    nullable=True),
            sa.Column("CreatedAt",          sa.DateTime(), server_default=sa.text("GETUTCDATE()"), nullable=False),
            schema="dbo",
        )

    # Drop new tables
    if _table_exists(conn, "HRBP_FraudFlags"):
        op.drop_table("HRBP_FraudFlags", schema="dbo")
    if _table_exists(conn, "Appr_FraudScores"):
        op.drop_table("Appr_FraudScores", schema="dbo")
    if _table_exists(conn, "P2P_FraudScores"):
        op.drop_table("P2P_FraudScores", schema="dbo")
