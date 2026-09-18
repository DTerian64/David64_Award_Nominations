"""Tenant-first blob paths for integrity model artifacts.

Every path begins with the authenticated tenant boundary. Model families and
immutable versions are always below that boundary; producers and consumers
must never reconstruct these strings independently.
"""

from __future__ import annotations

import re


_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _tenant(tenant_id: int) -> str:
    if int(tenant_id) <= 0:
        raise ValueError("tenant_id must be positive")
    return f"tenant_{int(tenant_id)}"


def _safe(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"Invalid {label}")
    return value


def tabular_bundle_prefix(tenant_id: int, model_version: str) -> str:
    version = _safe(model_version, _VERSION, "model_version")
    return f"{_tenant(tenant_id)}/tabular/{version}"


def tabular_manifest_blob(tenant_id: int, model_version: str) -> str:
    return f"{tabular_bundle_prefix(tenant_id, model_version)}/manifest.json"


def tabular_serving_model_blob(tenant_id: int, model_version: str) -> str:
    return f"{tabular_bundle_prefix(tenant_id, model_version)}/serving/model.pkl"


def tabular_serving_visualization_blob(
    tenant_id: int, model_version: str
) -> str:
    return (
        f"{tabular_bundle_prefix(tenant_id, model_version)}"
        "/serving/score_distribution.png"
    )


def gnn_bundle_prefix(tenant_id: int, model_version: str) -> str:
    version = _safe(model_version, _VERSION, "model_version")
    return f"{_tenant(tenant_id)}/gnn/{version}"


def gnn_manifest_blob(tenant_id: int, model_version: str) -> str:
    return f"{gnn_bundle_prefix(tenant_id, model_version)}/manifest.json"


def gnn_serving_decoder_blob(tenant_id: int, model_version: str) -> str:
    return f"{gnn_bundle_prefix(tenant_id, model_version)}/serving/decoder.pt"


def graph_bundle_prefix(tenant_id: int, run_id: str) -> str:
    run = _safe(run_id, _RUN_ID, "run_id")
    return f"{_tenant(tenant_id)}/graph/{run}"


def graph_inference_snapshot_blob(tenant_id: int, run_id: str) -> str:
    return f"{graph_bundle_prefix(tenant_id, run_id)}/inference-snapshot.json.gz"
