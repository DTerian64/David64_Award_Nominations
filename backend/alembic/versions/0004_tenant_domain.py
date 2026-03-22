"""Add Domain column to dbo.Tenants for domain-based tenant isolation

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-22

Changes
-------
Adds a nullable, unique NVARCHAR(253) column ``Domain`` to dbo.Tenants.

  Domain — the canonical public hostname for this tenant's front-end
  (e.g. "acme-awards.terian-services.com").  Used by the authentication
  layer to enforce that a tenant's users can only log in from their
  assigned domain.  NULL means no domain restriction is enforced for
  that tenant (useful while migrating / for internal tenants).

After running this migration, populate the Domain values with:

    UPDATE dbo.Tenants SET Domain = 'sandbox-awards.terian-services.com'
    WHERE TenantName = 'sandbox';   -- adjust TenantName to match your row

    UPDATE dbo.Tenants SET Domain = 'acme-awards.terian-services.com'
    WHERE TenantName = 'acme';      -- adjust TenantName to match your row

Do NOT hardcode TenantId values here — use TenantName or AzureAdTenantId
so the migration is re-runnable across environments without ID collision.

Downgrade
---------
Drops the Domain column (data is lost on downgrade).
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Idempotent — skip if Domain column already exists
    existing = conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'Tenants' "
        "AND COLUMN_NAME = 'Domain'"
    )).fetchone()

    if not existing:
        op.add_column(
            "Tenants",
            sa.Column(
                "Domain",
                sa.String(253),   # max valid DNS hostname length
                nullable=True,
            ),
            schema="dbo",
        )

    # Filtered unique index — enforces uniqueness only on non-NULL values.
    # A standard UNIQUE constraint treats multiple NULLs as duplicates in SQL
    # Server, which would fail immediately since all existing rows have Domain=NULL.
    # The WHERE clause excludes NULLs so tenants without a domain can coexist.
    # Idempotent — skip if the index already exists.
    index_exists = conn.execute(sa.text(
        "SELECT 1 FROM sys.indexes "
        "WHERE name = 'uq_tenants_domain' "
        "AND object_id = OBJECT_ID('dbo.Tenants')"
    )).fetchone()

    if not index_exists:
        conn.execute(sa.text(
            "CREATE UNIQUE INDEX uq_tenants_domain "
            "ON dbo.Tenants (Domain) "
            "WHERE Domain IS NOT NULL"
        ))

    conn.execute(sa.text("COMMIT"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_tenants_domain ON dbo.Tenants"))
    op.drop_column("Tenants", "Domain", schema="dbo")
