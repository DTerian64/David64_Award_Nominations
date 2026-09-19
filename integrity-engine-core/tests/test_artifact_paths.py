"""Tenant-first integrity artifact path contract."""

import pytest

from integrity_engine.artifact_paths import (
    gnn_manifest_blob,
    gnn_specialist_decoder_blob,
    gnn_serving_decoder_blob,
    graph_inference_snapshot_blob,
    tabular_manifest_blob,
    tabular_serving_model_blob,
    tabular_serving_visualization_blob,
)


def test_all_model_families_are_nested_below_tenant_boundary():
    assert tabular_manifest_blob(5, "tabular-v1-run") == (
        "tenant_5/tabular/tabular-v1-run/manifest.json"
    )
    assert tabular_serving_model_blob(5, "tabular-v1-run") == (
        "tenant_5/tabular/tabular-v1-run/serving/model.pkl"
    )
    assert tabular_serving_visualization_blob(5, "tabular-v1-run") == (
        "tenant_5/tabular/tabular-v1-run/serving/score_distribution.png"
    )
    assert gnn_manifest_blob(5, "gnn-v2-run") == (
        "tenant_5/gnn/gnn-v2-run/manifest.json"
    )
    assert gnn_serving_decoder_blob(5, "gnn-v2-run") == (
        "tenant_5/gnn/gnn-v2-run/serving/decoder.pt"
    )
    assert gnn_specialist_decoder_blob(5, "gnn-v3-run", "ring") == (
        "tenant_5/gnn/gnn-v3-run/specialists/ring/serving/decoder.pt"
    )
    assert graph_inference_snapshot_blob(5, "run-123") == (
        "tenant_5/graph/run-123/inference-snapshot.json.gz"
    )


@pytest.mark.parametrize("value", ["../escape", "nested/path", "", " space"])
def test_versions_and_run_ids_cannot_escape_tenant_prefix(value):
    with pytest.raises(ValueError):
        tabular_manifest_blob(5, value)
    with pytest.raises(ValueError):
        graph_inference_snapshot_blob(5, value)
