"""Strict contract for ``gnn.explanation.requested`` messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .errors import PermanentExtensionError

EVENT_TYPE = "gnn.explanation.requested"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ExplanationRequest:
    request_id: str
    nomination_id: int
    tenant_id: int
    model_version: str
    graph_snapshot_id: str
    source_message_id: str
    requested_at: str
    request_reason: str | None = None
    embedding_as_of: str | None = None
    scoring_policy_version: int | None = None

    @classmethod
    def parse(cls, payload: Any) -> "ExplanationRequest":
        if not isinstance(payload, dict):
            raise PermanentExtensionError("MESSAGE_BODY_NOT_OBJECT")
        if payload.get("event_type") != EVENT_TYPE:
            raise PermanentExtensionError("UNSUPPORTED_EVENT_TYPE")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise PermanentExtensionError("UNSUPPORTED_SCHEMA_VERSION")
        required = (
            "request_id", "nomination_id", "tenant_id", "gnn_model_version",
            "graph_snapshot_id", "source_message_id", "requested_at",
        )
        missing = [name for name in required if payload.get(name) in (None, "")]
        if missing:
            raise PermanentExtensionError("MISSING_REQUIRED_FIELDS:" + ",".join(sorted(missing)))
        try:
            tenant_id = int(payload["tenant_id"])
            nomination_id = int(payload["nomination_id"])
        except (TypeError, ValueError) as exc:
            raise PermanentExtensionError("INVALID_ENTITY_ID") from exc
        if tenant_id < 1 or nomination_id < 1:
            raise PermanentExtensionError("INVALID_ENTITY_ID")
        model_version = str(payload["gnn_model_version"])
        expected_id = f"gnnexp:t{tenant_id}:n{nomination_id}:{model_version}"
        if payload["request_id"] != expected_id:
            raise PermanentExtensionError("REQUEST_ID_MISMATCH")
        try:
            datetime.fromisoformat(str(payload["requested_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise PermanentExtensionError("INVALID_REQUESTED_AT") from exc
        policy_version = payload.get("gnn_scoring_policy_version")
        return cls(
            request_id=expected_id,
            nomination_id=nomination_id,
            tenant_id=tenant_id,
            model_version=model_version,
            graph_snapshot_id=str(payload["graph_snapshot_id"]),
            source_message_id=str(payload["source_message_id"]),
            requested_at=str(payload["requested_at"]),
            request_reason=payload.get("request_reason"),
            embedding_as_of=payload.get("embedding_as_of"),
            scoring_policy_version=int(policy_version) if policy_version is not None else None,
        )
