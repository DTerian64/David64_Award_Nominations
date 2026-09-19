"""Immutable GNN bundle loading with manifest, identity, size, and hash checks."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from typing import Protocol

import torch
from integrity_engine.artifact_paths import gnn_bundle_prefix

from .contracts import ExplanationRequest
from .errors import PermanentExtensionError, TransientExtensionError


class BlobReader(Protocol):
    def read(self, blob_name: str, max_bytes: int) -> bytes: ...


@dataclass(frozen=True)
class ArtifactBundle:
    manifest: dict
    snapshot: dict
    encoder: dict
    decoder: dict


class BundleLoader:
    def __init__(self, reader: BlobReader, max_artifact_bytes: int):
        self.reader = reader
        self.max_artifact_bytes = max_artifact_bytes

    def load(self, request: ExplanationRequest) -> ArtifactBundle:
        prefix = gnn_bundle_prefix(
            request.tenant_id, request.artifact_bundle_version
        )
        manifest_raw = self.reader.read(
            f"{prefix}/manifest.json", min(self.max_artifact_bytes, 10_000_000)
        )
        try:
            manifest = json.loads(manifest_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PermanentExtensionError("INVALID_MANIFEST_JSON") from exc
        self._validate_manifest(manifest, request)
        descriptors = {row.get("role"): row for row in manifest["artifacts"]}
        if len(descriptors) != len(manifest["artifacts"]):
            raise PermanentExtensionError("DUPLICATE_ARTIFACT_ROLE")
        encoder_role, decoder_role = self._serving_roles(request)
        required_roles = (
            "explanation_graph_snapshot", encoder_role, decoder_role
        )
        missing = [role for role in required_roles if role not in descriptors]
        if missing:
            raise PermanentExtensionError("MISSING_ARTIFACT_ROLES:" + ",".join(missing))
        loaded = {}
        for role in required_roles:
            descriptor = descriptors[role]
            path = descriptor.get("relative_path")
            if (
                not isinstance(path, str)
                or path.startswith(("/", "\\"))
                or ".." in path.split("/")
            ):
                raise PermanentExtensionError(f"INVALID_ARTIFACT_PATH:{role}")
            raw = self.reader.read(f"{prefix}/{path}", self.max_artifact_bytes)
            if len(raw) != int(descriptor.get("size_bytes", -1)):
                raise PermanentExtensionError(f"ARTIFACT_SIZE_MISMATCH:{role}")
            if hashlib.sha256(raw).hexdigest() != descriptor.get("sha256"):
                raise PermanentExtensionError(f"ARTIFACT_HASH_MISMATCH:{role}")
            try:
                loaded[role] = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
            except Exception as exc:
                raise PermanentExtensionError(f"UNSAFE_OR_INVALID_ARTIFACT:{role}") from exc
        self._validate_identity(manifest, loaded, request)
        return ArtifactBundle(
            manifest=manifest,
            snapshot=loaded["explanation_graph_snapshot"],
            encoder=loaded[encoder_role],
            decoder=loaded[decoder_role],
        )

    @staticmethod
    def _serving_roles(request: ExplanationRequest) -> tuple[str, str]:
        if not request.specialist_key:
            return "serving_encoder", "serving_decoder"
        prefix = f"specialist_{request.specialist_key.lower()}"
        return f"{prefix}_serving_encoder", f"{prefix}_serving_decoder"

    @staticmethod
    def _validate_manifest(manifest: dict, request: ExplanationRequest) -> None:
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise PermanentExtensionError("UNSUPPORTED_MANIFEST_SCHEMA")
        if manifest.get("artifact_type") != "graph_neural_network":
            raise PermanentExtensionError("INVALID_ARTIFACT_TYPE")
        if manifest.get("tenant_id") != request.tenant_id:
            raise PermanentExtensionError("MANIFEST_TENANT_MISMATCH")
        if manifest.get("model_version") != request.artifact_bundle_version:
            raise PermanentExtensionError("MANIFEST_MODEL_MISMATCH")
        if manifest.get("graph_snapshot_id") != request.graph_snapshot_id:
            raise PermanentExtensionError("MANIFEST_SNAPSHOT_MISMATCH")
        if not isinstance(manifest.get("artifacts"), list):
            raise PermanentExtensionError("INVALID_ARTIFACT_INDEX")
        if request.specialist_key:
            specialist = (manifest.get("specialists") or {}).get(
                request.specialist_key
            ) or {}
            if specialist.get("model_version") != request.model_version:
                raise PermanentExtensionError("SPECIALIST_MODEL_MISMATCH")
            selected = specialist.get("architecture")
        else:
            selected = (manifest.get("selection") or {}).get(
                "selected_architecture"
            )
        if selected not in {"graphsage", "gcn", "gatv2"}:
            raise PermanentExtensionError("NO_SUPPORTED_SELECTED_ARCHITECTURE")

    @staticmethod
    def _validate_identity(manifest: dict, loaded: dict, request: ExplanationRequest) -> None:
        if request.specialist_key:
            selected = manifest["specialists"][request.specialist_key][
                "architecture"
            ]
        else:
            selected = manifest["selection"]["selected_architecture"]
        encoder_role, decoder_role = BundleLoader._serving_roles(request)
        for role, artifact in loaded.items():
            if artifact.get("tenant_id", request.tenant_id) != request.tenant_id:
                raise PermanentExtensionError(f"ARTIFACT_TENANT_MISMATCH:{role}")
            expected_version = (
                request.artifact_bundle_version
                if role == "explanation_graph_snapshot"
                else request.model_version
            )
            if artifact.get("model_version") != expected_version:
                raise PermanentExtensionError(f"ARTIFACT_MODEL_MISMATCH:{role}")
            if artifact.get("graph_snapshot_id") != request.graph_snapshot_id:
                raise PermanentExtensionError(f"ARTIFACT_SNAPSHOT_MISMATCH:{role}")
            if artifact.get("feature_schema_version") != manifest.get("feature_schema_version"):
                raise PermanentExtensionError(f"FEATURE_SCHEMA_MISMATCH:{role}")
        if loaded[encoder_role].get("architecture") != selected:
            raise PermanentExtensionError("ENCODER_ARCHITECTURE_MISMATCH")
        if loaded[decoder_role].get("architecture") != selected:
            raise PermanentExtensionError("DECODER_ARCHITECTURE_MISMATCH")
        snapshot = loaded["explanation_graph_snapshot"]
        if snapshot.get("snapshot_schema_version") != 1:
            raise PermanentExtensionError("UNSUPPORTED_SNAPSHOT_SCHEMA")
        declared_relations = {
            tuple(relation)
            for relation in loaded[encoder_role].get("relations", [])
        }
        actual_relations = {
            (row.get("source"), row.get("relationship"), row.get("target"))
            for row in snapshot.get("edges", [])
        }
        if declared_relations and declared_relations != actual_relations:
            raise PermanentExtensionError("SNAPSHOT_RELATION_MISMATCH")


class AzureBlobReader:
    def __init__(self, container_client):
        self.container_client = container_client

    def read(self, blob_name: str, max_bytes: int) -> bytes:
        try:
            downloader = self.container_client.download_blob(blob_name)
            size = getattr(downloader.properties, "size", None)
            if size is not None and int(size) > max_bytes:
                raise PermanentExtensionError(f"ARTIFACT_TOO_LARGE:{blob_name}")
            data = downloader.readall()
        except PermanentExtensionError:
            raise
        except Exception as exc:
            raise TransientExtensionError(f"ARTIFACT_DOWNLOAD_FAILED:{blob_name}") from exc
        if len(data) > max_bytes:
            raise PermanentExtensionError(f"ARTIFACT_TOO_LARGE:{blob_name}")
        return data
