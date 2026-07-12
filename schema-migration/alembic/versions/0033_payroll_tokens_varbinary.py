"""Encrypt payroll_tokens: convert access_token / refresh_token to VARBINARY(MAX)

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-02

Changes
-------
dbo.payroll_tokens:
  - Truncate existing rows (plaintext tokens are no longer valid after
    encryption is enabled; the OAuth flow must be re-completed per tenant).
  - Drop TEXT columns access_token, refresh_token.
  - Add VARBINARY(MAX) columns access_token, refresh_token.

After this migration the payroll-broker will encrypt both values with
AES-256-GCM before writing and decrypt after reading.  The encryption key
is stored in Azure Key Vault (secret: PAYROLL-TOKEN-ENCRYPTION-KEY) and
injected into the container as PAYROLL_TOKEN_ENCRYPTION_KEY.
"""

import sqlalchemy as sa
from alembic import op


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing plaintext rows are invalid once encryption is enforced.
    # Tenants must re-complete the OAuth flow after deployment.
    op.execute("DELETE FROM dbo.payroll_tokens")

    # Drop old TEXT columns
    op.drop_column("payroll_tokens", "access_token",  schema="dbo")
    op.drop_column("payroll_tokens", "refresh_token", schema="dbo")

    # Add encrypted VARBINARY(MAX) columns
    op.add_column(
        "payroll_tokens",
        sa.Column("access_token",  sa.LargeBinary, nullable=False),
        schema="dbo",
    )
    op.add_column(
        "payroll_tokens",
        sa.Column("refresh_token", sa.LargeBinary, nullable=False),
        schema="dbo",
    )


def downgrade() -> None:
    op.drop_column("payroll_tokens", "access_token",  schema="dbo")
    op.drop_column("payroll_tokens", "refresh_token", schema="dbo")

    op.add_column(
        "payroll_tokens",
        sa.Column("access_token",  sa.Text, nullable=False),
        schema="dbo",
    )
    op.add_column(
        "payroll_tokens",
        sa.Column("refresh_token", sa.Text, nullable=False),
        schema="dbo",
    )
