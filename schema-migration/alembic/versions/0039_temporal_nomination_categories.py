"""Enable system-versioned temporal history on nomination_categories (SOC 2)

Revision ID: 0039
Revises: 0038
Create Date: 2026-07-16

nomination_categories became admin-editable (Setup > Award Categories) with
soft-delete (is_active) and per-category award limits, so its changes warrant
the same full temporal history as the other config/access tables (migration
0036). Indefinite history (config data, not secrets).

NOTE: like 0036, enabling SYSTEM_VERSIONING requires more than db_ddladmin on
this database, so the migration job (sql-migrations) will fail this step. Run
it once as the Entra admin (db_owner); the _is_temporal() guard then makes the
job skip it and record the revision.
"""

import sqlalchemy as sa
from alembic import op

revision      = "0039"
down_revision = "0038"
branch_labels = None
depends_on    = None

_TABLE   = "nomination_categories"
_HISTORY = "nomination_categories_History"


def _is_temporal(table: str) -> bool:
    conn = op.get_bind()
    return conn.execute(
        sa.text("SELECT temporal_type FROM sys.tables WHERE object_id = OBJECT_ID(:qn)"),
        {"qn": f"dbo.{table}"},
    ).scalar() == 2


def upgrade() -> None:
    if _is_temporal(_TABLE):
        return
    op.execute(f"""
        ALTER TABLE dbo.{_TABLE} ADD
            ValidFrom datetime2(3) GENERATED ALWAYS AS ROW START HIDDEN
                CONSTRAINT DF_{_TABLE}_ValidFrom DEFAULT SYSUTCDATETIME(),
            ValidTo   datetime2(3) GENERATED ALWAYS AS ROW END HIDDEN
                CONSTRAINT DF_{_TABLE}_ValidTo
                DEFAULT CONVERT(datetime2(3), '9999-12-31 23:59:59.999'),
            PERIOD FOR SYSTEM_TIME (ValidFrom, ValidTo);
    """)
    op.execute(f"""
        ALTER TABLE dbo.{_TABLE}
            SET (SYSTEM_VERSIONING = ON (HISTORY_TABLE = dbo.{_HISTORY}));
    """)


def downgrade() -> None:
    if not _is_temporal(_TABLE):
        return
    op.execute(f"ALTER TABLE dbo.{_TABLE} SET (SYSTEM_VERSIONING = OFF);")
    op.execute(f"ALTER TABLE dbo.{_TABLE} DROP PERIOD FOR SYSTEM_TIME;")
    op.execute(f"ALTER TABLE dbo.{_TABLE} DROP CONSTRAINT DF_{_TABLE}_ValidFrom;")
    op.execute(f"ALTER TABLE dbo.{_TABLE} DROP CONSTRAINT DF_{_TABLE}_ValidTo;")
    op.execute(f"ALTER TABLE dbo.{_TABLE} DROP COLUMN ValidFrom;")
    op.execute(f"ALTER TABLE dbo.{_TABLE} DROP COLUMN ValidTo;")
    op.execute(f"DROP TABLE dbo.{_HISTORY};")
