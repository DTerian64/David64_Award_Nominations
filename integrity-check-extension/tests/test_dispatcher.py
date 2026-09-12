from types import SimpleNamespace

import dispatcher


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
