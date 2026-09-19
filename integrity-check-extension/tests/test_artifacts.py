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
        model_version="v1", bundle_version="v1", artifact_bundle_version="v1",
        graph_snapshot_id="s1", source_message_id="m1",
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
    prefix = "tenant_1/gnn/v1/"
    return {prefix + "manifest.json": json.dumps(manifest).encode(),
            **{prefix + path: content for path, content in raw.items()}}


def test_bundle_loads_only_hash_validated_versioned_artifacts():
    bundle = BundleLoader(MemoryReader(_blobs()), 1_000_000).load(_request())
    assert bundle.encoder["architecture"] == "graphsage"


def test_hash_mismatch_is_permanent():
    blobs = _blobs()
    blobs["tenant_1/gnn/v1/snapshot.pt"] += b"tampered"
    with pytest.raises(PermanentExtensionError, match="ARTIFACT_SIZE_MISMATCH"):
        BundleLoader(MemoryReader(blobs), 1_000_000).load(_request())


def test_specialist_bundle_loads_track_specific_artifacts():
    request = ExplanationRequest(
        request_id="gnnexp:t1:n2:bundle-v3:RING:ring-v1",
        nomination_id=2,
        tenant_id=1,
        model_version="ring-v1",
        bundle_version="bundle-v3",
        artifact_bundle_version="bundle-v3",
        specialist_key="RING",
        graph_snapshot_id="s1",
        source_message_id="m1",
        requested_at="2026-09-11T00:00:00Z",
    )
    snapshot = {
        "model_version": "bundle-v3",
        "graph_snapshot_id": "s1",
        "feature_schema_version": "gnn-v2",
        "tenant_id": 1,
        "snapshot_schema_version": 1,
        "edges": [],
    }
    encoder = {
        "model_version": "ring-v1",
        "graph_snapshot_id": "s1",
        "feature_schema_version": "gnn-v2",
        "architecture": "gatv2",
    }
    decoder = {**encoder}
    values = {
        "snapshot.pt": snapshot,
        "specialists/ring/serving/encoder.pt": encoder,
        "specialists/ring/serving/decoder.pt": decoder,
    }
    raw = {path: _bytes(value) for path, value in values.items()}
    roles = (
        ("explanation_graph_snapshot", "snapshot.pt"),
        ("specialist_ring_serving_encoder", "specialists/ring/serving/encoder.pt"),
        ("specialist_ring_serving_decoder", "specialists/ring/serving/decoder.pt"),
    )
    manifest = {
        "schema_version": 1,
        "artifact_type": "graph_neural_network",
        "tenant_id": 1,
        "model_version": "bundle-v3",
        "graph_snapshot_id": "s1",
        "feature_schema_version": "gnn-v2",
        "specialists": {
            "RING": {"model_version": "ring-v1", "architecture": "gatv2"}
        },
        "artifacts": [
            {
                "role": role,
                "relative_path": path,
                "size_bytes": len(raw[path]),
                "sha256": hashlib.sha256(raw[path]).hexdigest(),
            }
            for role, path in roles
        ],
    }
    prefix = "tenant_1/gnn/bundle-v3/"
    blobs = {
        prefix + "manifest.json": json.dumps(manifest).encode(),
        **{prefix + path: content for path, content in raw.items()},
    }

    loaded = BundleLoader(MemoryReader(blobs), 1_000_000).load(request)
    assert loaded.decoder["model_version"] == "ring-v1"
