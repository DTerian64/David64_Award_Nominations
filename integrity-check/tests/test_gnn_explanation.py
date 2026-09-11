r"""Tests for the non-blocking GNN explanation request contract.

Usage (PowerShell):

    cd "C:\Users\David\source\repos\David64_Award_Nominations\Award_Nomination_App\integrity-check"
    python -m pytest tests/test_gnn_explanation.py -v
"""

from datetime import date, datetime, timezone

from inference import gnn_explanation


BASE_RESULT = {
    "model_available": True,
    "fraud_score": 72,
    "fraud_prob": 0.72,
    "risk_level": "HIGH",
    "model_version": "gnn-v2-20260909-t1",
    "embedding_as_of": date(2026, 9, 9),
    "graph_snapshot_id": "gnn-graph-v2-20260909-t1",
    "scoring_policy_version": 4,
}


def _plan(result=None, config=None):
    return gnn_explanation.plan(
        gnn_result=result or BASE_RESULT,
        gnn_policy=config or {},
        tenant_id=1,
        nomination_id=13881,
        source_message_id="source-message",
        now=datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc),
    )


def test_explanations_are_fail_closed_until_tenant_enables_them():
    planned = _plan()
    assert planned.should_publish is False
    assert planned.explanation == {
        "method": "GNNEXPLAINER",
        "status": "NOT_REQUESTED",
        "reason": "FEATURE_DISABLED",
    }


def test_enabled_medium_policy_builds_deterministic_pointer_message():
    planned = _plan(config={
        "explanation_enabled": True, "explanation_minimum_risk": "MEDIUM"
    })

    assert planned.should_publish is True
    assert planned.explanation["status"] == "REQUESTED"
    assert planned.event == {
        "event_type": "gnn.explanation.requested",
        "schema_version": 1,
        "request_id": "gnnexp:t1:n13881:gnn-v2-20260909-t1",
        "nomination_id": 13881,
        "tenant_id": 1,
        "gnn_model_version": "gnn-v2-20260909-t1",
        "embedding_as_of": "2026-09-09",
        "graph_snapshot_id": "gnn-graph-v2-20260909-t1",
        "request_reason": "MEDIUM_OR_HIGHER",
        "gnn_scoring_policy_version": 4,
        "source_message_id": "source-message",
        "requested_at": "2026-09-09T18:00:00Z",
    }


def test_below_configured_risk_does_not_publish():
    result = {**BASE_RESULT, "risk_level": "MEDIUM"}
    planned = _plan(result=result, config={
        "explanation_enabled": True, "explanation_minimum_risk": "HIGH"
    })
    assert planned.should_publish is False
    assert planned.explanation["reason"] == "BELOW_TRIGGER_RISK"


def test_v1_result_without_exact_snapshot_is_not_explained():
    result = {**BASE_RESULT, "graph_snapshot_id": None}
    planned = _plan(result=result, config={
        "explanation_enabled": True, "explanation_minimum_risk": "MEDIUM"
    })
    assert planned.should_publish is False
    assert planned.explanation["reason"] == "REPRODUCIBILITY_METADATA_UNAVAILABLE"


def test_unavailable_model_is_not_explained():
    result = {**BASE_RESULT, "model_available": False}
    planned = _plan(result=result, config={
        "explanation_enabled": True, "explanation_minimum_risk": "MEDIUM"
    })
    assert planned.should_publish is False
    assert planned.explanation["reason"] == "MODEL_UNAVAILABLE"


def test_publish_failure_is_bounded_and_retains_request_identity():
    planned = _plan(config={
        "explanation_enabled": True, "explanation_minimum_risk": "MEDIUM"
    })
    failed = gnn_explanation.publish_failed(planned.explanation, RuntimeError("boom"))
    assert failed["status"] == "FAILED"
    assert failed["reason"] == "PUBLISH_FAILED"
    assert failed["detail"] == "boom"
    assert failed["request_id"] == planned.explanation["request_id"]
