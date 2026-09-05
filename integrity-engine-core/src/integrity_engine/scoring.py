"""Shared, side-effect-free continuous scoring primitives."""

from __future__ import annotations

from typing import Mapping


def risk_level(score: float, thresholds: Mapping[str, float]) -> str:
    """Convert a numeric 0-100 score to the configured severity."""
    if score >= float(thresholds["critical"]):
        return "CRITICAL"
    if score >= float(thresholds["high"]):
        return "HIGH"
    if score >= float(thresholds["medium"]):
        return "MEDIUM"
    if score >= float(thresholds["low"]):
        return "LOW"
    return "NONE"


def continuous_score(
    *,
    base_score: float,
    minimum_score: float,
    maximum_score: float,
    parameters: Mapping[str, float],
    signals: Mapping[str, float],
) -> tuple[float, dict]:
    """Score normalized signals and retain the full deterministic derivation."""
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

