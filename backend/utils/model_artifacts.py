"""Read-only access to safe model representations in the ML model container."""

from __future__ import annotations

import json
import logging
import os
from typing import Literal, Optional


logger = logging.getLogger(__name__)

ModelComponent = Literal["rf", "gnn"]
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_VISUALIZATION_BYTES = 10 * 1024 * 1024


def _blob_service_client():
    from azure.storage.blob import BlobServiceClient

    account = os.getenv("AZURE_STORAGE_ACCOUNT", "awardnominationmodels")
    storage_key = os.getenv("AZURE_STORAGE_KEY")
    if storage_key:
        return BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=storage_key,
        )

    from azure.identity import DefaultAzureCredential

    return BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net",
        credential=DefaultAzureCredential(
            managed_identity_client_id=os.getenv("MI_CLIENT_ID")
        ),
    )


def _download(blob_name: str, maximum_bytes: int) -> Optional[bytes]:
    """Download one server-selected blob; return None when it does not exist."""
    from azure.core.exceptions import ResourceNotFoundError

    container = os.getenv("MODEL_CONTAINER", "ml-models")
    try:
        blob = _blob_service_client().get_blob_client(
            container=container,
            blob=blob_name,
        )
        properties = blob.get_blob_properties()
        if properties.size > maximum_bytes:
            raise ValueError(f"Model representation exceeds {maximum_bytes} bytes")
        payload = blob.download_blob().readall()
        if len(payload) > maximum_bytes:
            raise ValueError(f"Model representation exceeds {maximum_bytes} bytes")
        return payload
    except ResourceNotFoundError:
        logger.info("Model representation blob is not available: %s", blob_name)
        return None


def get_manifest(tenant_id: int, component: ModelComponent) -> Optional[dict]:
    """Return a validated JSON manifest for exactly one authenticated tenant."""
    names = {
        "rf": f"random_forest_tenant_{tenant_id}.manifest.json",
        "gnn": f"gnn_tenant_{tenant_id}.manifest.json",
    }
    payload = _download(names[component], _MAX_MANIFEST_BYTES)
    if payload is None:
        return None

    manifest = json.loads(payload.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Model manifest must be a JSON object")
    if manifest.get("tenant_id") != tenant_id:
        raise ValueError("Model manifest tenant does not match the authenticated tenant")
    expected_type = "random_forest" if component == "rf" else "graph_neural_network"
    if manifest.get("artifact_type") != expected_type:
        raise ValueError("Model manifest type is invalid")
    if manifest.get("schema_version") != 1:
        raise ValueError("Model manifest schema version is unsupported")
    if component == "rf":
        # Older RF manifests may describe the now-retired post-decision
        # Approver classifier. The active RF product is nomination-only.
        manifest = dict(manifest)
        models = dict(manifest.get("models") or {})
        retired_approver = models.pop("approver", None) is not None
        manifest["models"] = models
        if retired_approver:
            manifest["retired_components"] = ["approver"]
        training = dict(manifest.get("training") or {})
        training.pop("appr_auc", None)
        training.pop("approver_auc", None)
        manifest["training"] = training
    return manifest


def get_rf_visualization(tenant_id: int) -> Optional[bytes]:
    """Return the tenant's generated RF score-distribution PNG."""
    manifest = get_manifest(tenant_id, "rf")
    if manifest is None or "approver" in manifest.get("retired_components", []):
        # The old two-panel image contains an Approver score distribution. Do
        # not show it after retirement; the next RF run publishes a P2P-only PNG.
        return None
    return _download(
        f"random_forest_tenant_{tenant_id}.png",
        _MAX_VISUALIZATION_BYTES,
    )
