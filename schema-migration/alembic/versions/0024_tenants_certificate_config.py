"""Add certificate_config to dbo.Tenants

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-15

Context
-------
Per-tenant configuration for the award certificate feature, stored as a JSON
blob (NVARCHAR(MAX)).  Mirrors the desc_check_config pattern (rev 0020): a
typed dataclass + safe-defaults loader reads it in the backend.

  {
    "enabled":               true,                    // master switch (link + attachment)
    "attach_to_beneficiary": false,                   // attach PDF to the beneficiary email
    "template_blob":         "default_certificate.png" // template in certificate-templates
  }

NULL means "use all defaults" — and the defaults are feature OFF
(enabled=false, attach_to_beneficiary=false).  Existing tenants are therefore
unaffected until they explicitly opt in; no rows are seeded here.

Downgrade
---------
Drops the column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :t AND COLUMN_NAME = :c AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name, "c": column_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "Tenants", "certificate_config"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants "
            "ADD certificate_config NVARCHAR(MAX) NULL"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "Tenants", "certificate_config"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants DROP COLUMN certificate_config"
        ))
