from types import SimpleNamespace
from contextlib import contextmanager
import json

import pytest

import dispatcher
from extensions.gnn_explainer.contracts import ExplanationRequest
from extensions.gnn_explainer.errors import PermanentExtensionError


def _payload():
    return {
        "event_type": "gnn.explanation.requested", "schema_version": 1,
        "request_id": "gnnexp:t1:n2:v1", "nomination_id": 2, "tenant_id": 1,
        "gnn_model_version": "v1", "graph_snapshot_id": "s1",
        "source_message_id": "m1", "requested_at": "2026-09-11T00:00:00Z",
    }


def test_active_claim_is_deferred_without_lock_renewal(monkeypatch):
    context = SimpleNamespace(
        gnn_result={"explanation": {"status": "RUNNING"}},
        details={}, policy_configuration={},
    )
    monkeypatch.setattr(dispatcher.db, "load_request_context", lambda request: context)
    monkeypatch.setattr(dispatcher.db, "claim_request", lambda request, attempt: False)
    renewed = []
    result = dispatcher.dispatch(
        _payload(), 2, loader=None, on_claim=lambda: renewed.append(True)
    )
    assert result.settlement is dispatcher.Settlement.DEFER
    assert renewed == []


def test_completed_request_is_settled_idempotently(monkeypatch):
    context = SimpleNamespace(
        gnn_result={"explanation": {"status": "COMPLETED"}},
        details={}, policy_configuration={},
    )
    monkeypatch.setattr(dispatcher.db, "load_request_context", lambda request: context)
    monkeypatch.setattr(dispatcher.db, "claim_request", lambda request, attempt: False)
    monkeypatch.setattr(dispatcher.db, "insert_nomination_log", lambda *args, **kwargs: None)
    result = dispatcher.dispatch(_payload(), 2, loader=None)
    assert result.settlement is dispatcher.Settlement.COMPLETE


@pytest.mark.parametrize('snapshot_id', ['s1', 'wrong-snapshot'])
def test_request_context_has_no_enable_gate_but_still_checks_snapshot(monkeypatch, snapshot_id):
    request = ExplanationRequest.parse(_payload())
    result = {"model_version": "v1", "graph_snapshot_id": snapshot_id,
              "explanation": {"request_id": request.request_id}}
    row = (500, 1, '2026-09-11', 10, 20, json.dumps(result), '{}')
    statements = []
    cursor = SimpleNamespace(execute=lambda sql, *args: statements.append(sql),
                             fetchone=lambda: row)

    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=lambda: cursor)

    monkeypatch.setattr(dispatcher.db, '_get_conn', connection)
    if snapshot_id == 's1':
        context = dispatcher.db.load_request_context(request)
        assert context.gnn_result == result
    else:
        with pytest.raises(PermanentExtensionError, match='DECISION_SNAPSHOT_MISMATCH'):
            dispatcher.db.load_request_context(request)
    assert 'ExplanationEnabled' not in statements[0]


def test_unimplemented_attribution_is_explicitly_persisted_as_failure(monkeypatch):
    context = SimpleNamespace(gnn_result={},
                              details={"nominator_id": 10, "beneficiary_id": 20},
                              policy_configuration={})
    monkeypatch.setattr(dispatcher.db, 'load_request_context', lambda request: context)
    monkeypatch.setattr(dispatcher.db, 'claim_request', lambda *args: True)
    monkeypatch.setattr(dispatcher.db, 'get_versioned_embeddings', lambda *args: {})
    monkeypatch.setattr(dispatcher, 'reproduce', lambda **kwargs: SimpleNamespace(serving_probability=0.72))
    failures, logs = [], []
    monkeypatch.setattr(dispatcher.db, 'finish_request', lambda request, result: failures.append(result))
    monkeypatch.setattr(dispatcher.db, 'insert_nomination_log', lambda *args, **kwargs: logs.append(args))
    result = dispatcher.dispatch(_payload(), 1, loader=SimpleNamespace(load=lambda request: object()))
    assert result.settlement is dispatcher.Settlement.DEAD_LETTER
    assert failures[0]['status'] == 'FAILED'
    assert failures[0]['reason'] == 'EXPLANATION_ENGINE_NOT_DEPLOYED'
    assert any('GNN explanation failed' in row for row in logs)
