"""Safe, JSON-only metadata helpers for trained model artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping


MANIFEST_SCHEMA_VERSION = 1


def json_safe(value: Any) -> Any:
    """Convert common ML scalar/container values into strict JSON values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        return json_safe(value.item())
    return str(value)


def artifact_descriptor(path: Path, role: str) -> dict:
    """Describe an artifact without exposing its local or blob-storage path."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "file_name": path.name,
        "role": role,
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def state_dict_summary(state_dict: Mapping[str, Any]) -> dict:
    """Return tensor names/shapes and aggregate parameter counts."""
    tensors = []
    parameter_count = 0
    for name, tensor in state_dict.items():
        count = int(tensor.numel())
        parameter_count += count
        tensors.append({
            "name": name,
            "shape": [int(value) for value in tensor.shape],
            "dtype": str(tensor.dtype).removeprefix("torch."),
            "parameter_count": count,
        })
    return {
        "tensor_count": len(tensors),
        "parameter_count": parameter_count,
        "tensors": tensors,
    }


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    """Write strict JSON atomically enough for the job's ephemeral output volume."""
    payload = json_safe(dict(manifest))
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
