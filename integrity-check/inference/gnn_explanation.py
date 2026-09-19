"""Plan asynchronous GNN explanations without coupling them to routing.

The live integrity worker owns only the request side of this contract.  It
persists the lifecycle placeholder with the GNN result and, when a request is
eligible, publishes a pointer message for ``integrity-check-extension``.

The planner is deliberately pure: it does not access SQL, Blob Storage, or
Service Bus, which keeps eligibility and idempotency easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


EVENT_TYPE = "gnn.explanation.requested"
SCHEMA_VERSION = 1
SPECIALIST_SCHEMA_VERSION = 2
METHOD = "GNNEXPLAINER"

RISK_ORDER = {
    "NONE": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


@dataclass(frozen=True)
class ExplanationPlan:
    """The persisted lifecycle object and optional Service Bus request."""

    explanation: dict[str, Any]
    event: dict[str, Any] | None = None

    @property
    def should_publish(self) -> bool:
        return self.event is not None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.astimezone(timezone.utc).isoformat()
    elif hasattr(value, "isoformat"):
        value = value.isoformat()
    else:
        value = str(value)
    return value.replace("+00:00", "Z")


def _not_requested(reason: str) -> ExplanationPlan:
    return ExplanationPlan({
        "method": METHOD,
        "status": "NOT_REQUESTED",
        "reason": reason,
    })


def plan(
    *,
    gnn_result: dict,
    gnn_policy: dict | None,
    tenant_id: int,
    nomination_id: int,
    source_message_id: str,
    now: datetime | None = None,
) -> ExplanationPlan:
    """Return the explanation state and event for one persisted GNN verdict.

    Requests are fail-closed until a tenant explicitly enables them and the GNN
    result identifies an immutable graph snapshot.  This prevents the extension
    from explaining a different graph than the one that produced the score.
    """
    policy = gnn_policy if isinstance(gnn_policy, dict) else {}
    if not bool(policy.get("explanation_enabled", False)):
        return _not_requested("FEATURE_DISABLED")
    if not bool(gnn_result.get("model_available")):
        return _not_requested("MODEL_UNAVAILABLE")

    risk = str(gnn_result.get("risk_level") or "UNKNOWN").upper()
    minimum_risk = str(policy.get("explanation_minimum_risk", "MEDIUM")).upper()
    if minimum_risk not in RISK_ORDER:
        return _not_requested("INVALID_MINIMUM_RISK_CONFIG")
    if risk not in RISK_ORDER:
        return _not_requested("UNKNOWN_GNN_RISK")
    if RISK_ORDER[risk] < RISK_ORDER[minimum_risk]:
        return _not_requested("BELOW_TRIGGER_RISK")

    bundle_version = gnn_result.get("bundle_version") or gnn_result.get(
        "model_version"
    )
    model_version = gnn_result.get("model_version")
    specialist_key = None
    artifact_bundle_version = bundle_version
    if isinstance(gnn_result.get("specialists"), dict):
        decisive = (
            (gnn_result.get("aggregate") or {}).get("decisive_specialists")
            or []
        )
        if not decisive:
            return _not_requested("DECISIVE_SPECIALIST_UNAVAILABLE")
        specialist_key = str(decisive[0])
        specialist = gnn_result["specialists"].get(specialist_key) or {}
        if not specialist.get("available") or not specialist.get("model_version"):
            return _not_requested("DECISIVE_SPECIALIST_UNAVAILABLE")
        model_version = specialist["model_version"]
        artifact_bundle_version = specialist.get(
            "artifact_bundle_version", bundle_version
        )
    graph_snapshot_id = gnn_result.get("graph_snapshot_id")
    if not model_version or not bundle_version or not graph_snapshot_id:
        return _not_requested("REPRODUCIBILITY_METADATA_UNAVAILABLE")

    requested_at = _iso(now or datetime.now(timezone.utc))
    if specialist_key:
        request_id = (
            f"gnnexp:t{int(tenant_id)}:n{int(nomination_id)}:"
            f"{bundle_version}:{specialist_key}:{model_version}"
        )
    else:
        request_id = (
            f"gnnexp:t{int(tenant_id)}:n{int(nomination_id)}:{model_version}"
        )
    reason = f"{minimum_risk}_OR_HIGHER"
    event = {
        "event_type": EVENT_TYPE,
        "schema_version": (
            SPECIALIST_SCHEMA_VERSION if specialist_key else SCHEMA_VERSION
        ),
        "request_id": request_id,
        "nomination_id": int(nomination_id),
        "tenant_id": int(tenant_id),
        "gnn_model_version": str(model_version),
        "embedding_as_of": _iso(gnn_result.get("embedding_as_of")),
        "graph_snapshot_id": str(graph_snapshot_id),
        "request_reason": reason,
        "gnn_scoring_policy_version": gnn_result.get("scoring_policy_version"),
        "source_message_id": str(source_message_id),
        "requested_at": requested_at,
    }
    if specialist_key:
        event.update({
            "gnn_bundle_version": str(bundle_version),
            "gnn_artifact_bundle_version": str(artifact_bundle_version),
            "specialist_key": specialist_key,
        })
    explanation = {
        "method": METHOD,
        "status": "REQUESTED",
        "request_id": request_id,
        "requested_at": requested_at,
    }
    if specialist_key:
        explanation.update({
            "specialist_key": specialist_key,
            "specialist_model_version": str(model_version),
        })
    return ExplanationPlan(
        explanation=explanation,
        event=event,
    )


def publish_failed(explanation: dict, error: Exception) -> dict:
    """Return a bounded failure state after routing-safe publish failure."""
    return {
        **explanation,
        "status": "FAILED",
        "reason": "PUBLISH_FAILED",
        "detail": str(error)[:500],
        "last_attempt_at": _iso(datetime.now(timezone.utc)),
    }
