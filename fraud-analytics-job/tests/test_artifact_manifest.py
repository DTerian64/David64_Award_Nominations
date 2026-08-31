"""Safe model-manifest serialization tests."""

import json
import math
import tempfile
from pathlib import Path

import torch

from modeling.artifact_manifest import (
    artifact_descriptor,
    json_safe,
    state_dict_summary,
    write_manifest,
)


def test_json_safe_replaces_non_finite_metrics_with_null():
    assert json_safe({"good": 0.75, "missing": math.nan}) == {
        "good": 0.75,
        "missing": None,
    }


def test_state_dict_summary_contains_shapes_and_parameter_count():
    summary = state_dict_summary({
        "weight": torch.zeros((3, 4)),
        "bias": torch.zeros(3),
    })
    assert summary["tensor_count"] == 2
    assert summary["parameter_count"] == 15
    assert summary["tensors"][0]["shape"] == [3, 4]


def test_manifest_and_artifact_descriptor_are_strict_json():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        artifact = root / "model.bin"
        artifact.write_bytes(b"trusted model bytes")
        manifest_path = root / "model.manifest.json"

        descriptor = artifact_descriptor(artifact, "serving_model")
        write_manifest(manifest_path, {"metric": math.inf, "artifacts": [descriptor]})
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert payload["metric"] is None
        assert payload["artifacts"][0]["size_bytes"] == len(b"trusted model bytes")
        assert len(payload["artifacts"][0]["sha256"]) == 64
