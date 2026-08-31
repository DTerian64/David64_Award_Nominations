"""Enable system-versioned temporal history on access/security/config tables (SOC 2)

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-14

Context
-------
SOC 2 change-history for the most sensitive tables. SQL Server system-versioned
temporal tables record every row version (old values, every UPDATE, and the
final state before a DELETE) automatically, from any writer — the engine does
it, the app cannot skip it. Combined with the created_by/updated_by columns
(migration 0034) this yields who + what + when, and closes the UserRoles hard-
delete gap (a revoked role now leaves a full trail).

Tables
------
  UserRoles          -> UserRoles_History          (access control)
  Tenants            -> Tenants_History            (security/config)
  payroll_providers  -> payroll_providers_History  (money routing)
  payroll_tokens     -> payroll_tokens_History      (credentials; 6-month retention
                        so rotated-out encrypted tokens age out of history)

The period columns are HIDDEN, so SELECT * and ORM mappings are unaffected; the
app's INSERT/UPDATE/DELETE/MERGE keep working unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision      = "0036"
down_revision = "0035"
branch_labels = None
depends_on    = None

# (table, history_table, extra SYSTEM_VERSIONING options)
_TABLES = [
    ("UserRoles",         "UserRoles_History",         ""),
    ("Tenants",           "Tenants_History",           ""),
    ("payroll_providers", "payroll_providers_History", ""),
    ("payroll_tokens",    "payroll_tokens_History",    ", HISTORY_RETENTION_PERIOD = 6 MONTHS"),
]


def _is_temporal(table: str) -> bool:
    """True if the table is already a system-versioned temporal table (temporal_type = 2)."""
    conn = op.get_bind()
    return conn.execute(
        sa.text("SELECT temporal_type FROM sys.tables WHERE object_id = OBJECT_ID(:qn)"),
        {"qn": f"dbo.{table}"},
    ).scalar() == 2


def upgrade() -> None:
    for table, history, opts in _TABLES:
        if _is_temporal(table):
            continue
        op.execute(f"""
            ALTER TABLE dbo.{table} ADD
                ValidFrom datetime2(3) GENERATED ALWAYS AS ROW START HIDDEN
                    CONSTRAINT DF_{table}_ValidFrom DEFAULT SYSUTCDATETIME(),
                ValidTo   datetime2(3) GENERATED ALWAYS AS ROW END HIDDEN
                    CONSTRAINT DF_{table}_ValidTo
                    DEFAULT CONVERT(datetime2(3), '9999-12-31 23:59:59.999'),
                PERIOD FOR SYSTEM_TIME (ValidFrom, ValidTo);
        """)
        op.execute(f"""
            ALTER TABLE dbo.{table}
                SET (SYSTEM_VERSIONING = ON (HISTORY_TABLE = dbo.{history}{opts}));
        """)


def downgrade() -> None:
    for table, history, _ in _TABLES:
        if not _is_temporal(table):
            continue
        op.execute(f"ALTER TABLE dbo.{table} SET (SYSTEM_VERSIONING = OFF);")
        op.execute(f"ALTER TABLE dbo.{table} DROP PERIOD FOR SYSTEM_TIME;")
        op.execute(f"ALTER TABLE dbo.{table} DROP CONSTRAINT DF_{table}_ValidFrom;")
        op.execute(f"ALTER TABLE dbo.{table} DROP CONSTRAINT DF_{table}_ValidTo;")
        op.execute(f"ALTER TABLE dbo.{table} DROP COLUMN ValidFrom;")
        op.execute(f"ALTER TABLE dbo.{table} DROP COLUMN ValidTo;")
        op.execute(f"DROP TABLE dbo.{history};")
