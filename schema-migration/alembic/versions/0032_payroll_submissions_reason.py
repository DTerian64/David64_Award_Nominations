"""Add reason column to dbo.payroll_submissions

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-02

Changes
-------
1. dbo.payroll_submissions — add reason NVARCHAR(1000) NULL

   Stores the provider-supplied rejection message (or NULL on success).
   Enables fast triage without having to grep logs.

   Status lifecycle after this migration:
     submitted  — row upserted before calling the provider
     rejected   — provider returned an error; reason = provider message
     accepted   — provider accepted the payroll; completed_at = GETUTCDATE()
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers
revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payroll_submissions",
        sa.Column("reason", sa.NVARCHAR(1000), nullable=True),
        schema="dbo",
    )


def downgrade() -> None:
    op.drop_column("payroll_submissions", "reason", schema="dbo")
