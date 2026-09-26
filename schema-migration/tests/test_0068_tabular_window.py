import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

path = Path(__file__).resolve().parents[1] / "alembic/versions/0068_tabular_tenant_history_window.py"
spec = importlib.util.spec_from_file_location("migration_0068", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_adds_default_without_changing_other_settings_or_existing_window():
    before = {"graph_pattern": {"detection_window_days": 180}, "gnn": {"window_days": 270}, "other": [1, 2]}
    after = json.loads(migration._with_tabular_window(json.dumps(before)))
    assert after == {**before, "tabular": {"window_days": 365}}
    after["tabular"]["window_days"] = 90
    assert json.loads(migration._with_tabular_window(json.dumps(after))) == after
    assert json.loads(migration._with_tabular_window(None)) == {"tabular": {"window_days": 365}}


def test_upgrade_applies_to_all_tenants_without_touching_corpus_or_policies():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(1, None), (5, '{"gnn":{"window_days":365}}')]
    with patch.object(migration.op, "get_bind", return_value=conn):
        migration.upgrade()
    statements = [str(call.args[0]) for call in conn.execute.call_args_list]
    assert "ORDER BY TenantId" in statements[0]
    assert "WHERE TenantId" not in statements[0]
    assert all("Nominations" not in sql and "ScoringPolicies" not in sql for sql in statements)
    assert [call.args[1]["tenant_id"] for call in conn.execute.call_args_list[1:]] == [1, 5]
    assert migration.down_revision == "0067"
