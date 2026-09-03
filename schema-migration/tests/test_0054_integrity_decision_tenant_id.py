"""Render the tenant migration using the SQL Server dialect, without a database."""

import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).parents[1] / "alembic" / "versions"
    / "0054_integrity_decision_tenant_id.py"
)
SPEC = importlib.util.spec_from_file_location("migration_0054", MIGRATION_PATH)
MIGRATION = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MIGRATION)


def render_migration(monkeypatch, direction):
    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="mssql", opts={"as_sql": True, "output_buffer": buffer},
    )
    monkeypatch.setattr(MIGRATION, "op", Operations(context))
    getattr(MIGRATION, direction)()
    return buffer.getvalue()


def test_backfill_precedes_required_column_and_constraints(monkeypatch):
    sql = render_migration(monkeypatch, "upgrade")
    assert MIGRATION.down_revision == "0053"
    assert sql.index("THROW 50054") < sql.index("ADD [TenantId]")
    assert "WHERE t.TenantId IS NULL" in sql
    assert "LEFT JOIN dbo.Tenants t ON t.TenantId = u.TenantId" in sql
    assert "JOIN dbo.Nominations n ON n.NominationId = d.NominationId" in sql
    assert "JOIN dbo.Users u ON u.UserId = n.NominatorId" in sql
    assert sql.index("SET TenantId = u.TenantId") < sql.index("ALTER COLUMN [TenantId] INTEGER NOT NULL")
    assert "FOREIGN KEY([TenantId]) REFERENCES dbo.[Tenants] ([TenantId])" in sql
    assert "DEFAULT" not in sql
    assert "PRIMARY KEY" not in sql  # Preserve the existing nomination-level key.


def test_tenant_index_supports_recent_decision_lookup(monkeypatch):
    sql = render_migration(monkeypatch, "upgrade")
    assert "([TenantId], CreatedAt DESC)" in sql
    assert "INCLUDE ([FinalRoute], [CompositeRiskLevel], [CompositeScore], [ReviewScope])" in sql


def test_downgrade_removes_dependencies_before_column(monkeypatch):
    sql = render_migration(monkeypatch, "downgrade")
    assert sql.index("DROP INDEX") < sql.index("DROP CONSTRAINT") < sql.index("DROP COLUMN")
