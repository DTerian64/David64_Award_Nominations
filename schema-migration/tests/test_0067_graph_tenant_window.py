"""Tenant Graph window migration, without a live SQL connection."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0067_graph_tenant_window_180_days.py"
SPEC = importlib.util.spec_from_file_location("migration_0067", PATH)
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_window_change_preserves_routing_gnn_and_other_graph_settings():
    original = {
        "graph_pattern": {"detection_window_days": 365, "other_setting": 7},
        "score_routing": {"high_threshold": 60},
        "gnn": {"window_days": 365, "score_routing": {"high_threshold": 65}},
        "graph": {"score_routing": {"high_threshold": 75}},
    }
    updated = json.loads(MIGRATION._with_graph_window(json.dumps(original)))
    assert updated["graph_pattern"] == {"detection_window_days": 180, "other_setting": 7}
    for key in ("score_routing", "gnn", "graph"):
        assert updated[key] == original[key]


@pytest.mark.parametrize("raw", [None, "{}", '{"gnn":{"enabled":true}}'])
def test_adds_missing_graph_namespace(raw):
    assert json.loads(MIGRATION._with_graph_window(raw))["graph_pattern"] == {
        "detection_window_days": 180,
    }


def test_updates_all_tenants_and_only_unpublished_policy_windows(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(1, None, 180), (5, '{"gnn":{}}', 365)]
    monkeypatch.setattr(MIGRATION.op, "get_bind", lambda: conn)
    MIGRATION.upgrade()
    calls = conn.execute.call_args_list
    assert "CAST(t.integrity_config AS nvarchar(max))" in str(calls[0].args[0])
    assert [call.args[1]["tenant_id"] for call in calls[1:3]] == [1, 5]
    assert all(json.loads(call.args[1]["configuration"])["graph_pattern"]["detection_window_days"] == 180
               for call in calls[1:3])
    assert "WHERE Status='DRAFT'" in str(calls[-1].args[0])
    assert MIGRATION.down_revision == "0066"


def test_migrates_gnn_window_without_changing_its_existing_value():
    assert json.loads(MIGRATION._with_graph_window(None, 180))["gnn"]["window_days"] == 180
    existing = '{"gnn":{"window_days":365,"score_routing":{"high_threshold":65}}}'
    updated = json.loads(MIGRATION._with_graph_window(existing, 180))
    assert updated["gnn"] == {"window_days": 365, "score_routing": {"high_threshold": 65}}


def test_does_not_invent_gnn_configuration_for_tenant_without_policy():
    assert "gnn" not in json.loads(MIGRATION._with_graph_window(None))


def test_downgrade_does_not_guess_previous_windows():
    with pytest.raises(RuntimeError, match="forward configuration change"):
        MIGRATION.downgrade()
