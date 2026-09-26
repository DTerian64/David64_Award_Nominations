"""Policy UI and publishing honor separate tenant-owned history windows."""

from unittest.mock import MagicMock
import json

import pytest

from utils import sqlhelper2 as sql


def test_setup_window_updates_preserve_other_tenant_configuration(monkeypatch):
    session = _session(monkeypatch)
    configuration = {"graph_pattern": {"detection_window_days": 180},
                     "gnn": {"window_days": 365, "score_routing": {"high_threshold": 65}},
                     "tabular": {"window_days": 365}, "unrelated": {"preserve": True}}
    session.execute.return_value.fetchone.return_value = ('{"embed_model":"preserved"}', json.dumps(configuration))
    data = sql.get_fraud_settings(5)
    assert (data["graph_window_days"], data["gnn_window_days"], data["tabular_window_days"]) == (180, 365, 365)
    data.update(graph_window_days=90, tabular_window_days=180)
    sql.update_fraud_settings(5, data, "admin@example.com")
    written = json.loads(session.execute.call_args.args[1]["ic"])
    assert written["graph_pattern"]["detection_window_days"] == 90
    assert written["gnn"] == configuration["gnn"]
    assert written["tabular"]["window_days"] == 180
    assert written["unrelated"] == {"preserve": True}
    assert json.loads(session.execute.call_args.args[1]["dcc"])["embed_model"] == "preserved"
    session.commit.assert_called_once()


def test_old_setup_client_does_not_overwrite_omitted_windows(monkeypatch):
    session = _session(monkeypatch)
    existing = {"graph_pattern": {"detection_window_days": 90}, "gnn": {"window_days": 180}, "tabular": {"window_days": 270}}
    session.execute.return_value.fetchone.return_value = (None, json.dumps(existing))
    data = sql.get_fraud_settings(5)
    for key in ("graph_window_days", "gnn_window_days", "tabular_window_days"):
        del data[key]
    sql.update_fraud_settings(5, data, "admin@example.com")
    written = json.loads(session.execute.call_args.args[1]["ic"])
    for namespace in existing:
        assert written[namespace] == existing[namespace]


@pytest.mark.parametrize("field", ["graph_window_days", "gnn_window_days", "tabular_window_days"])
def test_window_api_rejects_non_positive_values(field):
    from routers.setup_router import FraudConfig
    from pydantic import ValidationError
    data = {"low_threshold": 20, "medium_threshold": 40, "high_threshold": 60,
            "critical_threshold": 80, "use_char_count": False, "min_char_count": 12,
            "min_word_count": 3, "category_alignment_threshold": .15,
            "duplicate_similarity_threshold": .85, "llm_category_check_enabled": False,
            "llm_fit_threshold": .4, field: 0}
    with pytest.raises(ValidationError):
        FraudConfig(**data)


def _session(monkeypatch):
    session = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = session
    monkeypatch.setattr(sql, "get_db_context", lambda: context)
    return session


@pytest.mark.parametrize("engine,namespace", [("graph", "graph_pattern"), ("gnn", "gnn")])
def test_publish_writes_only_its_own_tenant_window_and_commits_atomically(monkeypatch, engine, namespace):
    session = _session(monkeypatch)
    session.execute.return_value.scalar_one_or_none.return_value = 42
    assert getattr(sql, f"publish_{engine}_scoring_policy_draft")(5, "admin@example.com") == 42
    statement = str(session.execute.call_args.args[0])
    assert "UPDATE t SET integrity_config=JSON_MODIFY" in statement
    assert f"'$.{namespace}'" in statement
    assert "CAST(t.integrity_config AS nvarchar(max))" in statement
    other_namespace = "gnn" if engine == "graph" else "graph_pattern"
    assert f"'$.{other_namespace}" not in statement
    assert "score_routing" not in statement
    assert session.execute.call_args.args[1] == {"tid": 5, "policy_id": 42}
    session.commit.assert_called_once()


def test_graph_draft_initializes_from_tenant_window_not_legacy_policy(monkeypatch):
    session = _session(monkeypatch)
    existing, active, insert, copy = (MagicMock() for _ in range(4))
    existing.scalar_one_or_none.return_value = None
    active.fetchone.return_value = (4, 2, "MAX_RELEVANT_FINDING", 25, 50, 75, 100, 180, 14)
    insert.scalar_one.return_value = 8
    session.execute.side_effect = [existing, active, insert, copy]
    assert sql.create_graph_scoring_policy_draft(5, "admin@example.com") == 8
    assert "'$.graph_pattern.detection_window_days'" in str(session.execute.call_args_list[1].args[0])
    assert session.execute.call_args_list[2].args[1]["window"] == 180


def test_gnn_draft_initializes_from_separate_tenant_window(monkeypatch):
    session = _session(monkeypatch)
    existing, insert = MagicMock(), MagicMock()
    existing.scalar_one_or_none.return_value = None
    insert.scalar_one_or_none.return_value = 8
    session.execute.side_effect = [existing, insert]
    assert sql.create_gnn_scoring_policy_draft(5, "admin@example.com") == 8
    statement = str(session.execute.call_args.args[0])
    assert "'$.gnn.window_days'" in statement
    assert "graph_pattern" not in statement


def test_active_graph_ui_uses_tenant_window_while_history_keeps_recorded_values(monkeypatch):
    session = _session(monkeypatch)
    policies, patterns, requests = (MagicMock() for _ in range(3))
    policies.fetchall.return_value = [
        (4, 2, "ACTIVE", "MAX_RELEVANT_FINDING", 25, 50, 75, 100, 180, 14, *([None] * 6)),
        (3, 1, "RETIRED", "MAX_RELEVANT_FINDING", 25, 50, 75, 100, 365, 14, *([None] * 6)),
    ]
    patterns.fetchall.return_value = []
    requests.fetchall.return_value = []
    session.execute.side_effect = [policies, patterns, requests]
    bundle = sql.get_graph_scoring_policy_bundle(5)
    assert bundle["active_policy"]["detection_window_days"] == 180
    assert bundle["history"][1]["detection_window_days"] == 365
    statement = str(session.execute.call_args_list[0].args[0])
    assert "CASE WHEN p.Status='ACTIVE'" in statement
    assert "'$.graph_pattern.detection_window_days'" in statement
