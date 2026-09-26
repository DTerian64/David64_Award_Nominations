"""Tenant-owned Graph detector windows, shared by batch and live evaluation."""
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

WINDOWED_DETECTORS = (
    "Ring", "BipartiteDenseBlock", "TemporalBurst", "SuperNominator",
    "SuperBeneficiary", "CopyPaste", "HiddenCandidate", "LowRecognitionNominator",
)
CONFIG_KEYS = {name: "CopyPasteFraud" if name == "CopyPaste" else name
               for name in WINDOWED_DETECTORS}


def detector_windows(configuration: Mapping[str, Any], fallback_days: int = 180) -> dict[str, int]:
    """Resolve overrides; legacy snapshots without overrides retain their window."""
    fallback = configuration.get("detection_window_days", fallback_days)
    if isinstance(fallback, bool) or not isinstance(fallback, int) or fallback < 1:
        raise ValueError("Graph detection_window_days must be a positive integer")
    overrides = configuration.get("detector_windows", {})
    if not isinstance(overrides, Mapping):
        raise ValueError("Graph detector_windows must be an object")
    unknown = set(overrides) - set(WINDOWED_DETECTORS) - {"CopyPasteFraud"}
    if unknown:
        raise ValueError(f"Unknown Graph detector windows: {sorted(unknown)}")
    result = {}
    for name in WINDOWED_DETECTORS:
        key = CONFIG_KEYS[name]
        if key != name and key in overrides and name in overrides and overrides[key] != overrides[name]:
            raise ValueError("Conflicting CopyPaste and CopyPasteFraud windows")
        days = overrides.get(key, overrides.get(name, fallback))
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError(f"Graph {key} window must be a positive integer")
        result[name] = days
    return result


def filter_detector_history(records: list[dict], days: int, as_of: datetime) -> list[dict]:
    """Batch interval [as_of - days, as_of); timestamps are normalized to UTC."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    cutoff = as_of - timedelta(days=days)
    result = []
    for item in records:
        value = item["CreatedAt"]
        if not isinstance(value, datetime):
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if cutoff <= value < as_of:
            result.append(item)
    return result
