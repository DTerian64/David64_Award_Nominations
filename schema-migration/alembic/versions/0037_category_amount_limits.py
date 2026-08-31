"""Add per-category award amount limits to nomination_categories

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-16

Adds optional min_amount / max_amount (whole-currency-unit INTs) to each
nomination category. NULL means "no category-specific bound" — the nomination
then falls back to the tenant-level min_award / max_award (Tenants.Config).
Enforced server-side in nominations_router.create_nomination and surfaced to the
nomination form via /api/tenant/config.
"""

import sqlalchemy as sa
from alembic import op

revision      = "0037"
down_revision = "0036"
branch_labels = None
depends_on    = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    return conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).fetchone() is not None


def upgrade() -> None:
    if not _column_exists("nomination_categories", "min_amount"):
        op.add_column("nomination_categories", sa.Column("min_amount", sa.Integer(), nullable=True))
    if not _column_exists("nomination_categories", "max_amount"):
        op.add_column("nomination_categories", sa.Column("max_amount", sa.Integer(), nullable=True))


def downgrade() -> None:
    if _column_exists("nomination_categories", "max_amount"):
        op.drop_column("nomination_categories", "max_amount")
    if _column_exists("nomination_categories", "min_amount"):
        op.drop_column("nomination_categories", "min_amount")
