"""nomination_categories — per-tenant custom nomination categories

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-29

Context
-------
Premium and Enterprise tenants may define a custom list of nomination
categories (e.g. "Innovation", "Teamwork", "Leadership").  When at least
one category row exists for a tenant the nomination form shows a required
category dropdown; tenants with no rows see no category field at all.

New table
---------
dbo.nomination_categories
    id                   INT IDENTITY PK
    tenant_id            INT FK → dbo.Tenants(TenantId)  NOT NULL
    category_description NVARCHAR(256)                   NOT NULL

New column on dbo.Nominations
------------------------------
CategoryId               INT FK → dbo.nomination_categories(id)  NULL
    NULL for tenants that have no categories, and for all pre-existing rows.

Downgrade
---------
Drops the FK + CategoryId column from Nominations, then drops
dbo.nomination_categories.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── helpers ──────────────────────────────────────────────────────────────────

def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = :t AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name},
    )
    return result.fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :t AND COLUMN_NAME = :c AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name, "c": column_name},
    )
    return result.fetchone() is not None


# ── upgrade ──────────────────────────────────────────────────────────────────

def upgrade() -> None:
    conn = op.get_bind()

    # 1. Create dbo.nomination_categories
    if not _table_exists(conn, "nomination_categories"):
        op.create_table(
            "nomination_categories",
            sa.Column("id",                   sa.Integer(),      primary_key=True, autoincrement=True),
            sa.Column("tenant_id",            sa.Integer(),      sa.ForeignKey("Tenants.TenantId"), nullable=False),
            sa.Column("category_description", sa.Unicode(256),   nullable=False),
            schema="dbo",
        )

        conn.execute(sa.text(
            "CREATE INDEX ix_nomcat_tenant_id "
            "ON dbo.nomination_categories (tenant_id)"
        ))

    # 2. Add CategoryId to dbo.Nominations (nullable — no impact on existing rows)
    if not _column_exists(conn, "Nominations", "CategoryId"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Nominations "
            "ADD CategoryId INT NULL "
            "CONSTRAINT fk_nominations_categoryid "
            "REFERENCES dbo.nomination_categories(id)"
        ))


# ── downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    conn = op.get_bind()

    # Drop FK + column first
    if _column_exists(conn, "Nominations", "CategoryId"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Nominations "
            "DROP CONSTRAINT fk_nominations_categoryid"
        ))
        conn.execute(sa.text(
            "ALTER TABLE dbo.Nominations DROP COLUMN CategoryId"
        ))

    if _table_exists(conn, "nomination_categories"):
        op.drop_table("nomination_categories", schema="dbo")
