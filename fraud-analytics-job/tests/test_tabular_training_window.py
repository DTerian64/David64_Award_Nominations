from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from systems.award_nominations.modeling.tabular import stage as trainer
from systems.award_nominations.source.tenant_config import get_tenant_tabular_window


@pytest.mark.parametrize("stored,expected", [(None, 365), (180, 180), (365, 365)])
def test_loader_reads_separate_tabular_tenant_json(stored, expected):
    connection = MagicMock()
    connection.cursor.return_value.fetchone.return_value = (stored,)
    assert get_tenant_tabular_window(connection, 5) == expected
    statement, tenant = connection.cursor.return_value.execute.call_args.args
    assert "'$.tabular.window_days'" in statement
    assert "CAST(integrity_config AS nvarchar(max))" in statement
    assert "graph_pattern" not in statement and "gnn.window_days" not in statement
    assert tenant == 5


def test_loader_rejects_nonpositive_window():
    connection = MagicMock()
    connection.cursor.return_value.fetchone.return_value = (0,)
    with pytest.raises(ValueError, match="positive"):
        get_tenant_tabular_window(connection, 5)


def test_job_loads_warmup_history_and_passes_configured_window_to_shared_builder(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr(trainer, "connect_award", lambda: connection)
    monkeypatch.setattr(trainer, "connect_sentinel", lambda: MagicMock())
    monkeypatch.setattr(trainer, "get_tenants", lambda conn: [(5, "Synthetics Inc")])
    monkeypatch.setattr(trainer, "get_tenant_name", lambda conn, tenant: "Synthetics Inc")
    monkeypatch.setattr(trainer, "get_tenant_tabular_window", lambda conn, tenant: 270)
    monkeypatch.setattr(trainer, "get_tenant_embed_model", lambda tenant: "test-encoder")
    monkeypatch.setattr(trainer, "SentenceTransformer", lambda name: object())
    loader = MagicMock(return_value=SimpleNamespace())
    monkeypatch.setattr(trainer, "load_award_nomination_dataset", loader)
    builder = MagicMock()
    builder.build.return_value = SimpleNamespace(source_snapshot_id="fixture")
    monkeypatch.setattr(trainer, "AwardNominationTabularV1FeatureBuilder", lambda: builder)
    candidate_evaluations = {
        "random_forest": {
            "eligible": False,
            "guardrail_failures": ["PR_AUC_LIFT_BELOW_MINIMUM"],
        },
        "tabular_mlp": {
            "eligible": False,
            "guardrail_failures": ["PR_AUC_LIFT_BELOW_MINIMUM"],
        },
    }
    monkeypatch.setattr(trainer, "evaluate_tabular_candidates", lambda *args: SimpleNamespace(
        selection=SimpleNamespace(
            selected_architecture=None,
            selection_reason="NO_CANDIDATE_PASSED_GUARDRAILS",
            candidate_evaluations=candidate_evaluations,
        )))
    record = MagicMock()
    monkeypatch.setattr(trainer, "_record_status", record)
    result = trainer.process_tenant(5, "run-1")
    request = loader.call_args.args[0]
    assert request.tenant_id == 5
    assert request.window_days == 540
    assert builder.build.call_args.kwargs["window_days"] == 270
    assert record.call_args.kwargs["diagnostics"]["window_days"] == 270
    assert record.call_args.kwargs["attempt_status"] == "SKIPPED"
    assert "serving_version" not in record.call_args.kwargs
    assert result.status == "SKIPPED"
    assert result.reason_code == "NO_CANDIDATE_PASSED_GUARDRAILS"
    assert result.diagnostics == record.call_args.kwargs["diagnostics"]
    assert result.diagnostics["selection"] == candidate_evaluations
