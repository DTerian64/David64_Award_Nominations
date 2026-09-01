"""Versioned JSON contracts for the four integrity decision engines."""

from __future__ import annotations

import json
from typing import Any


ENGINE_SCHEMA_VERSION = 1
DECISION_SCHEMA_VERSION = 2

_PROVENANCE_FIELDS = (
    "registry_serving_status",
    "last_attempt_status",
    "last_attempt_at",
    "last_successful_at",
    "status_run_id",
    "status_updated_at",
    "last_serving_version",
)


def _provenance(result: dict) -> dict:
    return {
        key: result.get(key)
        for key in _PROVENANCE_FIELDS
        if key in result
    }


def _common(engine: str, result: dict) -> dict:
    available = bool(result.get("model_available"))
    return {
        "schema_version": ENGINE_SCHEMA_VERSION,
        "engine": engine,
        "available": available,
        "status": "AVAILABLE" if available else "UNAVAILABLE",
        "unavailable_reason": (
            None if available else result.get("unavailable_reason")
        ),
        "unavailable_detail": (
            None if available else result.get("unavailable_detail")
        ),
        "score": result.get("fraud_score") if available else None,
        "model_probability": result.get("fraud_prob") if available else None,
        "score_derivation": result.get("score_derivation"),
        "score_thresholds": result.get("score_thresholds"),
        "risk_level": result.get("risk_level") if available else "UNKNOWN",
        "flagged": bool(result.get("flagged")) if available else False,
        "findings": list(result.get("warning_flags") or []),
        "provenance": _provenance(result),
    }


def rf_result(result: dict) -> dict:
    payload = _common("RF", result)
    payload.update({
        "model_version": result.get("model_version"),
        "explanation": {
            "shap_status": result.get("shap_status"),
            "shap_reason": result.get("shap_reason"),
            "top_features": list(result.get("shap_explanations") or []),
            "llm_status": result.get("llm_explanation_status"),
            "llm_reason": result.get("llm_explanation_reason"),
            "llm_text": result.get("llm_explanation"),
        },
    })
    return payload


def graph_result(result: dict) -> dict:
    payload = _common("GRAPH", result)
    # A graph score is a rules-based severity mapping, never a probability.
    payload["model_probability"] = None
    payload.update({
        "source_severity": result.get("source_severity"),
        "snapshot_as_of": result.get("snapshot_as_of"),
        "snapshot_run_id": result.get("snapshot_run_id"),
        "snapshot_finding_count": result.get("snapshot_finding_count"),
        "snapshot_age_days": result.get("snapshot_age_days"),
        "affected_user_ids": list(result.get("affected_user_ids") or []),
        "pattern_findings": list(result.get("pattern_findings") or []),
        "winning_finding_hash": result.get("winning_finding_hash"),
        "winning_pattern_type": result.get("winning_pattern_type"),
        "scoring_strategy": result.get("scoring_strategy"),
        "scoring_policy_version": result.get("scoring_policy_version"),
        "score_thresholds": result.get("score_thresholds"),
        "score_derivation": result.get("score_derivation"),
    })
    return payload


def gnn_result(result: dict) -> dict:
    payload = _common("GNN", result)
    payload.update({
        "model_version": result.get("model_version"),
        "embedding_as_of": result.get("embedding_as_of"),
    })
    return payload


def semantic_result(result: Any) -> dict:
    evidence = getattr(result, "evidence", None)
    if isinstance(evidence, dict) and evidence.get("engine") == "SEMANTIC":
        return evidence
    return {
        "schema_version": ENGINE_SCHEMA_VERSION,
        "engine": "SEMANTIC",
        "available": False,
        "status": "UNAVAILABLE",
        "embedding": {"available": False, "status": "NOT_CAPTURED"},
        "llm": {"available": False, "status": "NOT_CAPTURED"},
        "duplicate_description": {
            "available": False,
            "status": "NOT_CAPTURED",
        },
        "combined_decision": {
            "action": getattr(result, "action", None),
            "checks": [],
            "reason": getattr(result, "reason", None),
        },
    }


def build(
    rf: dict,
    graph: dict,
    gnn: dict,
    semantic: Any,
) -> dict[str, dict]:
    """Build all four JSON-safe engine documents from one inference run."""
    payloads = {
        "rf": rf_result(rf),
        "graph": graph_result(graph),
        "gnn": gnn_result(gnn),
        "semantic": semantic_result(semantic),
    }
    # Fail before opening a transaction if a future engine accidentally adds a
    # non-serializable object to its persistence contract.
    json.dumps(payloads, default=str)
    return payloads


def dumps(payload: dict) -> str:
    return json.dumps(payload, default=str, separators=(",", ":"))
