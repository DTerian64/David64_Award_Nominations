"""Auditable routing policy for independent RF, graph, and GNN opinions."""

from __future__ import annotations

POLICY_VERSION = "max-severity-v1"

_RISK_RANK = {"UNKNOWN": -1, "NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def _risk(component: dict) -> str:
    risk = str(component.get("risk_level", "NONE")).upper()
    return risk if risk in _RISK_RANK and risk != "UNKNOWN" else "NONE"


def component_flags(name: str, component: dict) -> list[str]:
    prefix = f"[{name}]"
    return [flag if str(flag).startswith("[") else f"{prefix} {flag}"
            for flag in component.get("warning_flags", [])]


def combine(rf: dict, graph: dict, gnn: dict) -> dict:
    """Use the highest categorical risk among active, available components.

    Component scores are calibrated independently, so numeric averaging would
    be misleading.  The categorical maximum is conservative, deterministic,
    and preserves every component's tenant-specific score-to-risk mapping.
    """
    candidates = [("RF", rf), ("Graph", graph), ("GNN", gnn)]
    available = [(name, result) for name, result in candidates
                 if bool(result.get("model_available"))]

    if not available:
        return {
            "policy_version": POLICY_VERSION,
            "decision_available": False,
            "risk_level": "UNKNOWN",
            "final_score": 0,
            "decision_probability": None,
            "flagged": False,
            "participating_models": [],
            "decisive_models": [],
            "warning_flags": [],
        }

    highest = max(_RISK_RANK[_risk(result)] for _, result in available)
    final_risk = next(risk for risk, rank in _RISK_RANK.items() if rank == highest)
    decisive = [(name, result) for name, result in available if _risk(result) == final_risk]
    final_score = max(int(result.get("fraud_score") or 0) for _, result in decisive)
    probabilities = [result.get("fraud_prob") for _, result in decisive
                     if isinstance(result.get("fraud_prob"), (int, float))]

    flags: list[str] = []
    for name, result in available:
        flags.extend(component_flags(name, result))

    return {
        "policy_version": POLICY_VERSION,
        "decision_available": True,
        "risk_level": final_risk,
        "final_score": final_score,
        "decision_probability": max(probabilities) if probabilities else None,
        "flagged": final_risk in ("MEDIUM", "HIGH", "CRITICAL"),
        "participating_models": [name for name, _ in available],
        "decisive_models": [name for name, _ in decisive],
        "warning_flags": flags,
    }
