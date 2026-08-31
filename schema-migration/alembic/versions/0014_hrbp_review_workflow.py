"""HRBP review workflow — dbo.UserRoles, dbo.FraudFlags

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-20

Context
-------
Introduces the HRBP (HR Business Partner) fraud-review workflow.

When the ML model flags a nomination as MEDIUM / HIGH / CRITICAL risk the
nomination is held in a new ``PendingHRBPReview`` status rather than being
hard-blocked at submission time.  An HRBP reviews the queue and either
approves (nomination proceeds to manager) or rejects (nomination cancelled).

New tables
----------
dbo.UserRoles
    Maps application-level roles (e.g. 'HRBP') to Users.  Kept separate
    from Azure AD app roles so that HRBP assignments can be managed through
    the app's own admin UI without requiring Azure AD access.

dbo.FraudFlags
    Per-nomination fraud signal snapshot captured at submission time.
    Richer than dbo.FraudScores (which serves model retraining): includes
    FraudProbability, top-feature JSON, and feature-summary JSON so the
    HRBP review queue can display full context without a re-inference call.

Status notes
------------
``Nominations.Status`` is a plain varchar(50) with no CHECK constraint
(established in migration 0012).  The new valid value is:

    PendingHRBPReview  — awaiting HRBP decision (inserted between submission
                         and the normal Pending/manager-approval stage)

The existing happy-path statuses are unchanged:
    Pending → Approved → PaymentSubmitted → Paid

Downgrade
---------
Drops dbo.FraudFlags and dbo.UserRoles (in FK-safe order).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── helpers (identical pattern to earlier migrations) ────────────────────────

def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = :t AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name},
    )
    return result.fetchone() is not None


# ── upgrade ──────────────────────────────────────────────────────────────────

def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. dbo.UserRoles ─────────────────────────────────────────────────────
    # Stores application-level role assignments that complement Azure AD app
    # roles.  HRBP is self-service: admins assign it through the app UI
    # without needing Azure AD access.
    if not _table_exists(conn, "UserRoles"):
        op.create_table(
            "UserRoles",
            sa.Column("UserRoleId",  sa.Integer(),     primary_key=True, autoincrement=True),
            sa.Column("UserId",      sa.Integer(),     sa.ForeignKey("Users.UserId"), nullable=False),
            sa.Column("Role",        sa.String(50),    nullable=False),
            sa.Column("AssignedAt",  sa.DateTime(),    server_default=sa.text("GETUTCDATE()"), nullable=False),
            sa.Column("AssignedBy",  sa.Integer(),     sa.ForeignKey("Users.UserId"), nullable=True),
            schema="dbo",
        )

        # A user should hold each role at most once.
        conn.execute(sa.text(
            "ALTER TABLE dbo.UserRoles "
            "ADD CONSTRAINT uq_userroles_user_role UNIQUE (UserId, Role)"
        ))

        # Fast lookups: all users with a given role, and all roles for a user.
        conn.execute(sa.text(
            "CREATE INDEX ix_userroles_role ON dbo.UserRoles (Role)"
        ))
        conn.execute(sa.text(
            "CREATE INDEX ix_userroles_userid ON dbo.UserRoles (UserId)"
        ))

    # ── 2. dbo.FraudFlags ────────────────────────────────────────────────────
    # Snapshot of every ML inference result at nomination-submission time.
    # Written by the backend when a nomination is flagged (any risk level so
    # the HRBP queue can show full detail even for later-promoted rows).
    #
    # Kept separate from dbo.FraudScores, which is written by the weekly
    # fraud-analytics-job for historical / retraining purposes.
    if not _table_exists(conn, "FraudFlags"):
        op.create_table(
            "FraudFlags",
            sa.Column("FlagId",             sa.Integer(),     primary_key=True, autoincrement=True),
            sa.Column("NominationId",       sa.Integer(),     sa.ForeignKey("Nominations.NominationId"), nullable=False),
            sa.Column("FraudScore",         sa.Integer(),     nullable=False),           # 0–100
            sa.Column("FraudProbability",   sa.Float(),       nullable=False),           # 0.0–1.0
            sa.Column("RiskLevel",          sa.String(20),    nullable=False),           # NONE/LOW/MEDIUM/HIGH/CRITICAL/UNKNOWN
            sa.Column("WarningFlags",       sa.String(500),   nullable=True),            # comma-separated
            sa.Column("TopFeaturesJson",    sa.Text(),        nullable=True),            # JSON array [{feature, importance}]
            sa.Column("FeatureSummaryJson", sa.Text(),        nullable=True),            # JSON object from fraud_result['feature_summary']
            sa.Column("CreatedAt",          sa.DateTime(),    server_default=sa.text("GETUTCDATE()"), nullable=False),
            schema="dbo",
        )

        # One flag record per nomination (1:1 at submission time).
        conn.execute(sa.text(
            "ALTER TABLE dbo.FraudFlags "
            "ADD CONSTRAINT uq_fraudflags_nomination UNIQUE (NominationId)"
        ))

        conn.execute(sa.text(
            "CREATE INDEX ix_fraudflags_risklevel ON dbo.FraudFlags (RiskLevel)"
        ))


# ── downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    # Drop FraudFlags before UserRoles (no FK between them, but FK to
    # Nominations must go first if we were to drop Nominations — we're not).
    op.drop_table("FraudFlags",  schema="dbo")
    op.drop_table("UserRoles",   schema="dbo")
