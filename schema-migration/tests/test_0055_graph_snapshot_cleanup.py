"""SQL Server DDL and policy-copy safety checks; no live database."""
import importlib.util
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

spec = importlib.util.spec_from_file_location('migration_0055', Path(__file__).parents[1] / 'alembic/versions/0055_graph_snapshot_cleanup.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_ddl_preserves_history_and_removes_only_legacy_columns(monkeypatch):
    buffer = StringIO()
    context = MigrationContext.configure(dialect_name='mssql', opts={'as_sql': True, 'output_buffer': buffer})
    monkeypatch.setattr(migration, 'op', Operations(context))
    monkeypatch.setattr(migration, '_check_dependencies', lambda: None)
    monkeypatch.setattr(migration, '_publish_policies', lambda: None)
    migration.upgrade()
    sql = buffer.getvalue()
    assert 'SnapshotComplete' in sql and 'DEFAULT 0' in sql
    assert '([TenantId], [RunId], [FindingHash])' in sql
    assert 'DELETE FROM' not in sql
    assert 'CREATE TABLE' not in sql
    for column in migration.LEGACY_COLUMNS:
        assert f'DROP COLUMN [{column}]' in sql
    assert 'GRAPH_REFRESH_REQUIRED' in sql


def test_dependencies_block_cleanup(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [('dbo.ExternalConsumer',)]
    monkeypatch.setattr(migration.op, 'get_bind', lambda: conn)
    with pytest.raises(RuntimeError, match='ExternalConsumer'):
        migration._check_dependencies()


def test_publishes_new_version_preserving_parameters(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(10, 1)]
    conn.execute.return_value.scalar_one.return_value = 11
    monkeypatch.setattr(migration.op, 'get_bind', lambda: conn)
    migration._publish_policies()
    sql = '\n'.join(str(call.args[0]) for call in conn.execute.call_args_list)
    assert "SET Status='RETIRED'" in sql
    assert 'MAX(PolicyVersion)+1' in sql
    assert "CASE WHEN PatternType='SuperBeneficiary' THEN 1 ELSE EnabledForRouting END" in sql
    assert 'ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore' in sql
    assert "p.Status='DRAFT'" in sql


def test_downgrade_refuses_to_discard_complete_history():
    with pytest.raises(RuntimeError, match='irreversible'):
        migration.downgrade()
