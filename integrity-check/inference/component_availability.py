"""Translate producer status into a consistent inference availability contract."""

from __future__ import annotations


def unavailable_metadata(
    component: str,
    fallback_reason: str,
    status: dict | None,
    *,
    source_missing: bool = False,
) -> dict:
    """Return stable reason/provenance fields for an unavailable component."""
    reason = fallback_reason.upper()
    detail = None

    if status and source_missing:
        if status.get("serving_status") == "AVAILABLE":
            reason = "SNAPSHOT_MISSING" if component == "GRAPH" else "ARTIFACT_MISSING"
            detail = (
                f"Registry reports {component} available, but inference could not load "
                f"its {'snapshot' if component == 'GRAPH' else 'artifact'}."
            )
        elif status.get("reason_code"):
            reason = status["reason_code"]
            detail = status.get("reason_detail")

    metadata = {
        "availability_status": "UNAVAILABLE",
        "unavailable_reason": reason,
        "unavailable_detail": detail,
    }
    if status:
        metadata.update({
            "registry_serving_status": status.get("serving_status"),
            "last_attempt_status": status.get("last_attempt_status"),
            "last_attempt_at": status.get("last_attempt_at"),
            "last_successful_at": status.get("last_successful_at"),
            "status_run_id": status.get("run_id"),
            "status_updated_at": status.get("updated_at"),
            "last_serving_version": status.get("serving_version"),
        })
    return metadata


def available_metadata(status: dict | None = None) -> dict:
    """Common fields for a component that produced a live opinion."""
    metadata = {
        "availability_status": "AVAILABLE",
        "unavailable_reason": None,
        "unavailable_detail": None,
    }
    if status:
        metadata.update({
            "registry_serving_status": status.get("serving_status"),
            "last_attempt_status": status.get("last_attempt_status"),
            "last_attempt_at": status.get("last_attempt_at"),
            "last_successful_at": status.get("last_successful_at"),
            "status_run_id": status.get("run_id"),
            "status_updated_at": status.get("updated_at"),
        })
    return metadata
