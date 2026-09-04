"""Reset scope and restored uniqueness; no live database writes."""
import importlib.util
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

spec = importlib.util.spec_from_file_location('migration_0056', Path(__file__).parents[1] / 'alembic/versions/0056_graph_evidence_identity.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def _connection(monkeypatch, *, dependencies=(), outside=0, existing=100):
    conn = MagicMock()
    conn.execute.return_value.scalar_one.side_effect = ['Sandbox', outside, existing]
    conn.execute.return_value.fetchall.return_value = dependencies
    monkeypatch.setattr(migration.op, 'get_bind', lambda: conn)


def test_reset_requires_exact_database_confirmation(monkeypatch):
    _connection(monkeypatch)
    monkeypatch.setenv('GRAPH_FINDINGS_RESET_DATABASE', 'WrongDatabase')
    with pytest.raises(RuntimeError, match='exact target database'):
        migration._preflight()


def test_confirmed_scope_is_allowed(monkeypatch):
    _connection(monkeypatch)
    monkeypatch.setenv('GRAPH_FINDINGS_RESET_DATABASE', 'Sandbox')
    migration._preflight()


def test_dependencies_block_reset(monkeypatch):
    _connection(monkeypatch, dependencies=[('ReviewFindingForeignKey',)])
    with pytest.raises(RuntimeError, match='references/triggers'):
        migration._preflight()


def test_other_tenants_block_reset(monkeypatch):
    _connection(monkeypatch, outside=1)
    with pytest.raises(RuntimeError, match='outside approved tenants'):
        migration._preflight()


def test_empty_install_needs_no_destructive_confirmation(monkeypatch):
    _connection(monkeypatch, existing=0)
    monkeypatch.delenv('GRAPH_FINDINGS_RESET_DATABASE', raising=False)
    migration._preflight()


def test_reset_ddl_preserves_tables_and_nomination_history(monkeypatch):
    buffer = StringIO()
    context = MigrationContext.configure(dialect_name='mssql', opts={'as_sql': True, 'output_buffer': buffer})
    monkeypatch.setattr(migration, 'op', Operations(context))
    monkeypatch.setattr(migration, '_preflight', lambda: None)
    migration.upgrade()
    sql = buffer.getvalue()
    assert 'DELETE FROM dbo.GraphPatternFindings WHERE TenantId IN (1,2,3)' in sql
    assert 'DELETE FROM dbo.UserGraphFlags WHERE TenantId IN (1,2,3)' in sql
    assert '([TenantId], [FindingHash])' in sql
    assert 'RunId=NULL, DiagnosticsJson=NULL' in sql
    assert 'DROP TABLE' not in sql and 'TRUNCATE' not in sql and 'CHECKIDENT' not in sql
    assert 'IntegrityDecisionResults' not in sql and 'Nomination_Logs' not in sql


def test_reset_cannot_be_undone_without_backup():
    with pytest.raises(RuntimeError, match='irreversible'):
        migration.downgrade()
