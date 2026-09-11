"""Deterministic operational selection for the tenant GNN architecture bake-off."""

from __future__ import annotations

import math
from typing import Any


SELECTION_POLICY_VERSION = "gnn-candidate-selection-v1"
SELECTION_METRIC = "holdout_pr_auc"
GRAPH_ARCHITECTURES = ("graphsage", "gcn", "gatv2")


def _finite_metric(candidate: dict[str, Any]) -> float | None:
    value = candidate.get("eval_pr_auc")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def select_architecture(
    candidates: dict[str, dict[str, Any]],
    *,
    incumbent_architecture: str | None = None,
    minimum_improvement_over_mlp: float = 0.02,
    incumbent_tie_tolerance: float = 0.01,
    minimum_eligible_graph_candidates: int = 2,
) -> dict[str, Any]:
    """Select one graph architecture, or explicitly decline activation.

    The MLP is an admission baseline only. It can prevent a graph model from
    being activated, but can never become the serving GNN engine.
    """
    evaluated: dict[str, dict[str, Any]] = {}
    for architecture, raw in candidates.items():
        row = dict(raw)
        failures = list(row.get("guardrail_failures") or [])
        metric = _finite_metric(row)
        if row.get("status") != "COMPLETED":
            failures.append("TRAINING_NOT_COMPLETED")
        if metric is None:
            failures.append("HOLDOUT_PR_AUC_UNAVAILABLE")
        row["eligible"] = not failures
        row["guardrail_failures"] = sorted(set(failures))
        evaluated[architecture] = row

    policy = {
        "policy_version": SELECTION_POLICY_VERSION,
        "selection_metric": SELECTION_METRIC,
        "minimum_improvement_over_mlp": minimum_improvement_over_mlp,
        "incumbent_tie_tolerance": incumbent_tie_tolerance,
        "minimum_eligible_graph_candidates": minimum_eligible_graph_candidates,
    }
    baseline = evaluated.get("mlp")
    baseline_value = _finite_metric(baseline or {})
    eligible_graphs = {
        name: row
        for name, row in evaluated.items()
        if name in GRAPH_ARCHITECTURES and row["eligible"]
    }

    def result(reason: str, selected: str | None = None) -> dict[str, Any]:
        selected_value = (
            _finite_metric(eligible_graphs[selected]) if selected else None
        )
        return {
            **policy,
            "selected_architecture": selected,
            "selected_metric_value": selected_value,
            "mlp_baseline_value": baseline_value,
            "improvement_over_mlp": (
                selected_value - baseline_value
                if selected_value is not None and baseline_value is not None
                else None
            ),
            "selection_reason": reason,
            "incumbent_architecture": incumbent_architecture,
            "candidates": evaluated,
        }

    if baseline is None or not baseline.get("eligible"):
        return result("INSUFFICIENT_ELIGIBLE_CANDIDATES")
    if len(eligible_graphs) < minimum_eligible_graph_candidates:
        completed_graph_count = sum(
            name in GRAPH_ARCHITECTURES and row.get("status") == "COMPLETED"
            for name, row in evaluated.items()
        )
        reason = (
            "CANDIDATE_GUARDRAIL_FAILED"
            if completed_graph_count >= minimum_eligible_graph_candidates
            else "INSUFFICIENT_ELIGIBLE_CANDIDATES"
        )
        return result(reason)

    admitted = {
        name: row
        for name, row in eligible_graphs.items()
        if _finite_metric(row) >= baseline_value + minimum_improvement_over_mlp
    }
    if not admitted:
        return result("NO_GRAPH_VALUE_OVER_MLP")

    best = max(admitted, key=lambda name: (_finite_metric(admitted[name]), name))
    if incumbent_architecture in admitted and incumbent_architecture != best:
        incumbent_value = _finite_metric(admitted[incumbent_architecture])
        best_value = _finite_metric(admitted[best])
        if best_value - incumbent_value <= incumbent_tie_tolerance:
            return result(
                "INCUMBENT_RETAINED_WITHIN_TOLERANCE",
                incumbent_architecture,
            )
    return result("HIGHEST_ELIGIBLE_PR_AUC", best)
