"""Deterministic scoring for one Graph Analytics detector finding."""

from __future__ import annotations

from typing import Mapping


def derive_graph_finding_severity(
    finding_score: float,
    thresholds: Mapping[str, float],
) -> str:
    """Convert a Graph finding's numeric 0-100 score to its severity."""
    if finding_score >= float(thresholds["critical"]):
        return "CRITICAL"
    if finding_score >= float(thresholds["high"]):
        return "HIGH"
    if finding_score >= float(thresholds["medium"]):
        return "MEDIUM"
    if finding_score >= float(thresholds["low"]):
        return "LOW"
    return "NONE"


def calculate_graph_finding_score(
    *,
    base_score: float,
    minimum_score: float,
    maximum_score: float,
    parameters: Mapping[str, float],
    signals: Mapping[str, float],
) -> tuple[float, dict]:
    """Calculate one detector finding's score and deterministic derivation."""
    normalized = {
        name: max(0.0, min(1.0, float(raw_value)))
        for name, raw_value in signals.items()
    }
    weights = {
        name: float(parameters.get(f"{name}_weight", 0.0))
        for name in normalized
    }
    contributions = {
        name: round(value * weights[name], 4)
        for name, value in normalized.items()
    }
    score = round(
        max(
            float(minimum_score),
            min(float(maximum_score), float(base_score) + sum(contributions.values())),
        ),
        2,
    )
    return score, {
        "base_score": float(base_score),
        "signals": {name: round(value, 4) for name, value in normalized.items()},
        "weights": weights,
        "contributions": contributions,
        "finding_score": score,
    }
