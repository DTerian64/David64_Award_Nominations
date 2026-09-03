"""Store the owning tenant on canonical integrity decisions.

Revision ID: 0054
Revises: 0053
Create Date: 2026-09-03

Pause integrity-check consumers for this cutover, apply the migration, deploy
the tenant-aware writer, then resume. Older writers cannot populate the new
required column. TenantId is derived from the nominator; never default it to 1.
Existing authorization joins and the one-decision-per-nomination PK remain.
"""

import sqlalchemy as sa
from alembic import op


revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail before changing the schema if any decision has no valid tenant.
    op.execute(sa.text("""
        IF EXISTS (
            SELECT 1
            FROM dbo.IntegrityDecisionResults d
            LEFT JOIN dbo.Nominations n ON n.NominationId = d.NominationId
            LEFT JOIN dbo.Users u ON u.UserId = n.NominatorId
            LEFT JOIN dbo.Tenants t ON t.TenantId = u.TenantId
            WHERE t.TenantId IS NULL
        )
            THROW 50054, '0054 cannot resolve the tenant for every integrity decision.', 1;
    """))
    op.add_column(
        "IntegrityDecisionResults",
        sa.Column("TenantId", sa.Integer(), nullable=True),
        schema="dbo",
    )
    op.execute(sa.text("""
        UPDATE d
        SET TenantId = u.TenantId
        FROM dbo.IntegrityDecisionResults d
        JOIN dbo.Nominations n ON n.NominationId = d.NominationId
        JOIN dbo.Users u ON u.UserId = n.NominatorId;
    """))
    op.alter_column(
        "IntegrityDecisionResults", "TenantId",
        existing_type=sa.Integer(), nullable=False, schema="dbo",
    )
    op.create_foreign_key(
        "FK_IntegrityDecisionResults_Tenant", "IntegrityDecisionResults", "Tenants",
        ["TenantId"], ["TenantId"], source_schema="dbo", referent_schema="dbo",
    )
    op.create_index(
        "IX_IntegrityDecisionResults_TenantCreated", "IntegrityDecisionResults",
        ["TenantId", sa.text("CreatedAt DESC")], schema="dbo",
        mssql_include=["FinalRoute", "CompositeRiskLevel", "CompositeScore", "ReviewScope"],
    )


def downgrade() -> None:
    op.drop_index(
        "IX_IntegrityDecisionResults_TenantCreated",
        table_name="IntegrityDecisionResults", schema="dbo",
    )
    op.drop_constraint(
        "FK_IntegrityDecisionResults_Tenant", "IntegrityDecisionResults",
        type_="foreignkey", schema="dbo",
    )
    op.drop_column("IntegrityDecisionResults", "TenantId", schema="dbo")
