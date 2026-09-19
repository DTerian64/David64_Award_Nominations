import pytest

from extensions.gnn_explainer.contracts import ExplanationRequest
from extensions.gnn_explainer.errors import PermanentExtensionError


def payload():
    return {
        "event_type": "gnn.explanation.requested",
        "schema_version": 1,
        "request_id": "gnnexp:t3:n13879:gnn-v2-test",
        "nomination_id": 13879,
        "tenant_id": 3,
        "gnn_model_version": "gnn-v2-test",
        "graph_snapshot_id": "snapshot-test",
        "source_message_id": "source-1",
        "requested_at": "2026-09-11T12:00:00Z",
    }


def test_valid_request_is_typed():
    request = ExplanationRequest.parse(payload())
    assert request.tenant_id == 3
    assert request.model_version == "gnn-v2-test"


@pytest.mark.parametrize("field", ["tenant_id", "graph_snapshot_id", "requested_at"])
def test_required_fields_fail_closed(field):
    value = payload()
    value.pop(field)
    with pytest.raises(PermanentExtensionError, match="MISSING_REQUIRED_FIELDS"):
        ExplanationRequest.parse(value)


def test_request_id_must_be_deterministic():
    value = payload()
    value["request_id"] = "random"
    with pytest.raises(PermanentExtensionError, match="REQUEST_ID_MISMATCH"):
        ExplanationRequest.parse(value)


def test_specialist_request_carries_bundle_and_model_identity():
    value = payload()
    value.update({
        "schema_version": 2,
        "gnn_bundle_version": "gnn-v3-bundle",
        "gnn_model_version": "gnn-v3-ring",
        "specialist_key": "RING",
        "request_id": "gnnexp:t3:n13879:gnn-v3-bundle:RING:gnn-v3-ring",
    })
    request = ExplanationRequest.parse(value)
    assert request.bundle_version == "gnn-v3-bundle"
    assert request.artifact_bundle_version == "gnn-v3-bundle"
    assert request.model_version == "gnn-v3-ring"
    assert request.specialist_key == "RING"
