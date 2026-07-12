"""Create dbo.EmailTemplates — per-tenant, per-language email + certificate templates

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-15

Context
-------
Moves the hard-coded email (and certificate) text out of code into a table so
templates can vary per tenant and per language. Resolution falls back along:

    (tenant, key, lang) -> (tenant, key, 'en') -> (1, key, lang) -> (1, key, 'en')

TenantId = 1 is the canonical/default org and holds the system default rows.
A seed script populates the TenantId=1 / Lang='en' defaults from the current
templates so the cutover is behaviour-neutral.

BodyTemplate is a Jinja2 source string (HTML for emails; a small JSON of labels
for the 'certificate' key). Subject is NULL for non-email keys.

Downgrade drops the table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, name: str) -> bool:
    return conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t"
    ), {"t": name}).fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "EmailTemplates"):
        op.create_table(
            "EmailTemplates",
            sa.Column("TemplateId",   sa.Integer(), sa.Identity(start=1, increment=1), primary_key=True),
            sa.Column("TenantId",     sa.Integer(), nullable=False),
            sa.Column("TemplateKey",  sa.Unicode(64),  nullable=False),
            sa.Column("Lang",         sa.Unicode(10),  nullable=False),
            sa.Column("Subject",      sa.Unicode(400), nullable=True),
            sa.Column("BodyTemplate", sa.UnicodeText(), nullable=False),   # NVARCHAR(MAX)
            sa.Column("Active",       sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("Version",      sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("UpdatedAt",    sa.DateTime(), nullable=False, server_default=sa.text("SYSUTCDATETIME()")),
            sa.Column("UpdatedBy",    sa.Unicode(256), nullable=True),
            sa.ForeignKeyConstraint(["TenantId"], ["dbo.Tenants.TenantId"], name="FK_EmailTemplates_Tenants"),
            sa.UniqueConstraint("TenantId", "TemplateKey", "Lang", name="UQ_EmailTemplates_tenant_key_lang"),
            schema="dbo",
        )
        # Lookup index for the resolver (TemplateKey first — every query filters on it).
        conn.execute(sa.text(
            "CREATE INDEX ix_emailtemplates_key_tenant_lang "
            "ON dbo.EmailTemplates (TemplateKey, TenantId, Lang) "
            "INCLUDE (Active)"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "EmailTemplates"):
        op.drop_table("EmailTemplates", schema="dbo")
