import hashlib
import io
import json

import pytest
import torch

from extensions.gnn_explainer.artifacts import BundleLoader
from extensions.gnn_explainer.contracts import ExplanationRequest
from extensions.gnn_explainer.errors import PermanentExtensionError


class MemoryReader:
    def __init__(self, blobs):
        self.blobs = blobs

    def read(self, name, max_bytes):
        value = self.blobs[name]
        assert len(value) <= max_bytes
        return value


def _request():
    return ExplanationRequest(
        request_id="gnnexp:t1:n2:v1", nomination_id=2, tenant_id=1,
        model_version="v1", graph_snapshot_id="s1", source_message_id="m1",
        requested_at="2026-09-11T00:00:00Z",
    )


def _bytes(value):
    output = io.BytesIO()
    torch.save(value, output)
    return output.getvalue()


def _blobs():
    common = {"model_version": "v1", "graph_snapshot_id": "s1", "feature_schema_version": "gnn-v2"}
    values = {
        "snapshot.pt": {
            **common, "tenant_id": 1, "snapshot_schema_version": 1, "edges": []
        },
        "serving/encoder.pt": {**common, "architecture": "graphsage"},
        "serving/decoder.pt": {**common, "architecture": "graphsage"},
    }
    raw = {path: _bytes(value) for path, value in values.items()}
    manifest = {
        "schema_version": 1, "artifact_type": "graph_neural_network",
        "tenant_id": 1, "model_version": "v1", "graph_snapshot_id": "s1",
        "feature_schema_version": "gnn-v2",
        "selection": {"selected_architecture": "graphsage"},
        "artifacts": [
            {"role": role, "relative_path": path, "size_bytes": len(raw[path]),
             "sha256": hashlib.sha256(raw[path]).hexdigest()}
            for role, path in (
                ("explanation_graph_snapshot", "snapshot.pt"),
                ("serving_encoder", "serving/encoder.pt"),
                ("serving_decoder", "serving/decoder.pt"),
            )
        ],
    }
    prefix = "gnn/tenant_1/v1/"
    return {prefix + "manifest.json": json.dumps(manifest).encode(),
            **{prefix + path: content for path, content in raw.items()}}


def test_bundle_loads_only_hash_validated_versioned_artifacts():
    bundle = BundleLoader(MemoryReader(_blobs()), 1_000_000).load(_request())
    assert bundle.encoder["architecture"] == "graphsage"


def test_hash_mismatch_is_permanent():
    blobs = _blobs()
    blobs["gnn/tenant_1/v1/snapshot.pt"] += b"tampered"
    with pytest.raises(PermanentExtensionError, match="ARTIFACT_SIZE_MISMATCH"):
        BundleLoader(MemoryReader(blobs), 1_000_000).load(_request())
