"""Tenant- and source-system-scoped blob paths for integrity model artifacts.

Every path begins with the authenticated tenant boundary followed by the source
system. Model families and immutable versions are always below those boundaries;
producers and consumers must never reconstruct these strings independently.
"""

from __future__ import annotations

import re


_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SYSTEM = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _tenant(tenant_id: int) -> str:
    if int(tenant_id) <= 0:
        raise ValueError("tenant_id must be positive")
    return f"tenant_{int(tenant_id)}"


def _safe(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"Invalid {label}")
    return value


def _system_prefix(tenant_id: int, system: str) -> str:
    system_name = _safe(system, _SYSTEM, "system")
    return f"{_tenant(tenant_id)}/{system_name}"


def tabular_bundle_prefix(
    tenant_id: int, model_version: str, *, system: str
) -> str:
    version = _safe(model_version, _VERSION, "model_version")
    return f"{_system_prefix(tenant_id, system)}/tabular/{version}"


def tabular_manifest_blob(
    tenant_id: int, model_version: str, *, system: str
) -> str:
    return (
        f"{tabular_bundle_prefix(tenant_id, model_version, system=system)}"
        "/manifest.json"
    )


def tabular_serving_model_blob(
    tenant_id: int, model_version: str, *, system: str
) -> str:
    return (
        f"{tabular_bundle_prefix(tenant_id, model_version, system=system)}"
        "/serving/model.pkl"
    )


def tabular_serving_visualization_blob(
    tenant_id: int, model_version: str, *, system: str
) -> str:
    return (
        f"{tabular_bundle_prefix(tenant_id, model_version, system=system)}"
        "/serving/score_distribution.png"
    )


def gnn_bundle_prefix(
    tenant_id: int, model_version: str, *, system: str
) -> str:
    version = _safe(model_version, _VERSION, "model_version")
    return f"{_system_prefix(tenant_id, system)}/gnn/{version}"


def gnn_manifest_blob(
    tenant_id: int, model_version: str, *, system: str
) -> str:
    return (
        f"{gnn_bundle_prefix(tenant_id, model_version, system=system)}"
        "/manifest.json"
    )


def gnn_serving_decoder_blob(
    tenant_id: int, model_version: str, *, system: str
) -> str:
    return (
        f"{gnn_bundle_prefix(tenant_id, model_version, system=system)}"
        "/serving/decoder.pt"
    )


def gnn_specialist_decoder_blob(
    tenant_id: int, bundle_version: str, specialist_key: str, *, system: str
) -> str:
    key = _safe(specialist_key.lower(), _VERSION, "specialist_key")
    return (
        f"{gnn_bundle_prefix(tenant_id, bundle_version, system=system)}"
        f"/specialists/{key}/serving/decoder.pt"
    )


def graph_bundle_prefix(tenant_id: int, run_id: str, *, system: str) -> str:
    run = _safe(run_id, _RUN_ID, "run_id")
    return f"{_system_prefix(tenant_id, system)}/graph/{run}"


def graph_inference_snapshot_blob(
    tenant_id: int, run_id: str, *, system: str
) -> str:
    return (
        f"{graph_bundle_prefix(tenant_id, run_id, system=system)}"
        "/inference-snapshot.json.gz"
    )
