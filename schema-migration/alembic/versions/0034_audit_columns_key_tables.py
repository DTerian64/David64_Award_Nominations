"""Add SOC 2 audit columns (created_at, created_by, updated_at, updated_by) to key tables

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-13

Context
-------
SOC 2 hardening: every business-critical ("key") table gets a uniform audit
quartet so *who* and *when* a row was created and last modified is always on
the record.

Convention
----------
Audit fields are snake_case, in deliberate visual contrast with the PascalCase
business columns:

    created_at   DATETIME       NOT NULL  DEFAULT SYSUTCDATETIME()
    created_by   NVARCHAR(256)  NULL      -- effective UPN, or 'svc:<name>'
    updated_at   DATETIME       NOT NULL  DEFAULT SYSUTCDATETIME()
    updated_by   NVARCHAR(256)  NULL

The *_at columns are DB-defaulted, so they are always populated even before the
application write-paths are wired. The *_by columns are set by the application
(the DB cannot derive the app user — all containers connect as the same Managed
Identity); historical rows are backfilled to 'backfill'. Tightening *_by to
NOT NULL is deferred to a later migration, once the app reliably populates them.

Key tables (9)
--------------
    Tenants, Users, UserRoles, Nominations, nomination_categories,
    EmailTemplates, payroll_providers, payroll_tokens, payroll_submissions

Per-table delta (idempotent — every add/rename is guarded):
  * Most tables    -> add all 4.
  * payroll_tokens -> already has created_at/updated_at -> add the *_by pair only.
  * EmailTemplates -> already has PascalCase UpdatedAt/UpdatedBy -> rename to
                      snake_case, then add created_at/created_by.
  * Nominations    -> the business field NominationDate is left UNTOUCHED;
                      created_at is added and backfilled to equal NominationDate.
                      Both are immutable after creation, so they stay equal.

Analytics/ML tables, the Service Bus idempotency log (ProcessedEvents), the
Impersonation_AuditLog, and reference data (Holidays) are intentionally excluded.
"""

import sqlalchemy as sa
from alembic import op

revision      = "0034"
down_revision = "0033"
branch_labels = None
depends_on    = None

_BY_TYPE     = sa.Unicode(256)
_UTC_DEFAULT = sa.text("SYSUTCDATETIME()")

# Tables that receive the full audit quartet.
_FULL = [
    "Tenants",
    "Users",
    "UserRoles",
    "Nominations",
    "nomination_categories",
    "payroll_providers",
    "payroll_submissions",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _column_exists(table: str, column: str) -> bool:
    """Case-INSENSITIVE column check (matches SQL Server identifier semantics)."""
    conn = op.get_bind()
    return conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).fetchone() is not None


def _column_exists_cs(table: str, column: str) -> bool:
    """Case-SENSITIVE column check — detects PascalCase names that need renaming."""
    conn = op.get_bind()
    return conn.execute(
        sa.text(
            "SELECT 1 FROM sys.columns "
            "WHERE object_id = OBJECT_ID(:qualified) "
            "  AND name = :c COLLATE Latin1_General_CS_AS"
        ),
        {"qualified": f"dbo.{table}", "c": column},
    ).fetchone() is not None


def _add_created_at(table: str) -> None:
    if not _column_exists(table, "created_at"):
        op.add_column(table, sa.Column("created_at", sa.DateTime(),
                                       server_default=_UTC_DEFAULT, nullable=False))


def _add_updated_at(table: str) -> None:
    if not _column_exists(table, "updated_at"):
        op.add_column(table, sa.Column("updated_at", sa.DateTime(),
                                       server_default=_UTC_DEFAULT, nullable=False))


def _add_created_by(table: str) -> None:
    if not _column_exists(table, "created_by"):
        op.add_column(table, sa.Column("created_by", _BY_TYPE, nullable=True))


def _add_updated_by(table: str) -> None:
    if not _column_exists(table, "updated_by"):
        op.add_column(table, sa.Column("updated_by", _BY_TYPE, nullable=True))


def _backfill_by(table: str) -> None:
    conn = op.get_bind()
    conn.execute(sa.text(f"UPDATE dbo.[{table}] SET created_by = 'backfill' WHERE created_by IS NULL"))
    conn.execute(sa.text(f"UPDATE dbo.[{table}] SET updated_by = 'backfill' WHERE updated_by IS NULL"))


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    conn = op.get_bind()

    # 1. Full-quartet tables.
    for t in _FULL:
        _add_created_at(t)
        _add_created_by(t)
        _add_updated_at(t)
        _add_updated_by(t)

    # 2. payroll_tokens already has created_at/updated_at -> add the *_by pair only.
    _add_created_by("payroll_tokens")
    _add_updated_by("payroll_tokens")

    # 3. EmailTemplates: normalize PascalCase -> snake_case, then add the create pair.
    if _column_exists_cs("EmailTemplates", "UpdatedAt"):
        op.execute("EXEC sp_rename 'dbo.EmailTemplates.UpdatedAt', 'updated_at', 'COLUMN'")
    if _column_exists_cs("EmailTemplates", "UpdatedBy"):
        op.execute("EXEC sp_rename 'dbo.EmailTemplates.UpdatedBy', 'updated_by', 'COLUMN'")
    _add_updated_at("EmailTemplates")   # defensive: no-op when the rename already produced it
    _add_updated_by("EmailTemplates")
    _add_created_at("EmailTemplates")
    _add_created_by("EmailTemplates")

    # 4. Nominations: created_at must equal the existing business field NominationDate.
    conn.execute(sa.text(
        "UPDATE dbo.Nominations SET created_at = NominationDate WHERE NominationDate IS NOT NULL"
    ))

    # 5. For tables whose updated_at was NEWLY added, seed it to created_at so existing
    #    rows start aligned. EmailTemplates/payroll_tokens keep their real updated_at.
    for t in _FULL:
        conn.execute(sa.text(f"UPDATE dbo.[{t}] SET updated_at = created_at"))

    # 6. Backfill *_by sentinels for historical rows across every key table
    #    (only fills NULLs, so real EmailTemplates.updated_by values are preserved).
    for t in _FULL + ["payroll_tokens", "EmailTemplates"]:
        _backfill_by(t)


def downgrade() -> None:
    # EmailTemplates: reverse the snake_case rename first.
    if _column_exists_cs("EmailTemplates", "updated_at"):
        op.execute("EXEC sp_rename 'dbo.EmailTemplates.updated_at', 'UpdatedAt', 'COLUMN'")
    if _column_exists_cs("EmailTemplates", "updated_by"):
        op.execute("EXEC sp_rename 'dbo.EmailTemplates.updated_by', 'UpdatedBy', 'COLUMN'")

    # EmailTemplates: drop only the create pair this migration added.
    for c in ("created_by", "created_at"):
        if _column_exists("EmailTemplates", c):
            op.drop_column("EmailTemplates", c, mssql_drop_default=True)

    # payroll_tokens: drop only the *_by pair this migration added.
    for c in ("updated_by", "created_by"):
        if _column_exists("payroll_tokens", c):
            op.drop_column("payroll_tokens", c, mssql_drop_default=True)

    # Full-quartet tables: drop all 4.
    for t in _FULL:
        for c in ("updated_by", "updated_at", "created_by", "created_at"):
            if _column_exists(t, c):
                op.drop_column(t, c, mssql_drop_default=True)
