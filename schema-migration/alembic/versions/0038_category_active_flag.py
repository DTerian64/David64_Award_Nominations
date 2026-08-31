"""Add is_active flag to nomination_categories (soft-delete)

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-16

Categories can't be deleted (Nominations.CategoryId references them), so an
is_active flag retires a category instead: new nominations only offer active
categories, while historic nominations keep resolving their (now-inactive) one.
Existing rows default to active.
"""

import sqlalchemy as sa
from alembic import op

revision      = "0038"
down_revision = "0037"
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
    if not _column_exists("nomination_categories", "is_active"):
        op.add_column(
            "nomination_categories",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        )


def downgrade() -> None:
    if _column_exists("nomination_categories", "is_active"):
        op.drop_column("nomination_categories", "is_active", mssql_drop_default=True)
