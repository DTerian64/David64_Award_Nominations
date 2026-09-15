"""Orchestrate non-promoting diagnostics for graph-specific model value."""

from __future__ import annotations

import logging
from typing import Iterable

import numpy as np

from ..contracts import CandidateSpec
from .feature_profiles import FULL_CAUSAL, MLP_CAUSAL, MLP_TABULAR
from .rolling_folds import evaluate_candidate_over_folds
from .scenarios import evaluate_scenarios

logger = logging.getLogger(__name__)

EVALUATOR_VERSION = "graph-value-by-ablation-v1"


def _difference(left: object, right: object) -> float | None:
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    return float(left) - float(right)


def evaluate_graph_value(
    *,
    folds: list[dict],
    y_train_by_fold: list[np.ndarray],
    y_eval_by_fold: list[np.ndarray],
    graph_architectures: Iterable[str],
    scenario_by_nomination_id: dict[int, str],
    hidden_dim: int,
    emb_dim: int,
    epochs: int,
) -> dict:
    """Measure engineered-feature and message-passing value independently.

    The result is diagnostic evidence only. It is neither read by, nor passed
    to, the operational holdout PR-AUC selection evaluator.
    """
    specs: dict[str, CandidateSpec] = {
        "mlp_tabular": {
            "architecture": "mlp",
            "feature_profile": MLP_TABULAR,
        },
        "mlp_causal": {
            "architecture": "mlp",
            "feature_profile": MLP_CAUSAL,
        },
        **{
            architecture: {
                "architecture": architecture,
                "feature_profile": FULL_CAUSAL,
            }
            for architecture in dict.fromkeys(graph_architectures)
        },
    }
    candidates: dict[str, dict] = {}
    all_predictions: list[dict] = []
    for candidate_name, spec in specs.items():
        logger.info("GNN graph-value diagnostic starting: %s", candidate_name)
        try:
            result, predictions = evaluate_candidate_over_folds(
                candidate_name=candidate_name,
                architecture=spec["architecture"],
                feature_profile=spec["feature_profile"],
                folds=folds,
                y_train_by_fold=y_train_by_fold,
                y_eval_by_fold=y_eval_by_fold,
                hidden_dim=hidden_dim,
                emb_dim=emb_dim,
                epochs=epochs,
            )
            candidates[candidate_name] = result
            all_predictions.extend(predictions)
        except Exception as exc:
            logger.exception("GNN graph-value diagnostic failed: %s", candidate_name)
            candidates[candidate_name] = {
                "status": "FAILED",
                "architecture": spec["architecture"],
                "feature_profile": spec["feature_profile"],
                "reason": type(exc).__name__,
                "detail": str(exc)[:500],
            }

    tabular = candidates.get("mlp_tabular", {})
    causal = candidates.get("mlp_causal", {})
    completed_graphs = {
        name: row
        for name, row in candidates.items()
        if name not in {"mlp_tabular", "mlp_causal"}
        and row.get("status") == "COMPLETED"
        and isinstance(row.get("macro_pr_auc"), (int, float))
    }
    best_graph = (
        max(completed_graphs, key=lambda name: completed_graphs[name]["macro_pr_auc"])
        if completed_graphs else None
    )
    best_graph_value = (
        completed_graphs[best_graph]["macro_pr_auc"] if best_graph else None
    )
    causal_value = causal.get("macro_pr_auc")
    tabular_value = tabular.get("macro_pr_auc")
    completed_count = sum(row.get("status") == "COMPLETED" for row in candidates.values())
    return {
        "schema_version": 1,
        "evaluator": EVALUATOR_VERSION,
        "role": "DIAGNOSTIC_ONLY",
        "affects_serving_selection": False,
        "status": "COMPLETED" if completed_count == len(candidates) else "PARTIAL",
        "metric": "macro_rolling_origin_pr_auc",
        "ablation": {
            "mlp_tabular_value": tabular_value,
            "mlp_causal_value": causal_value,
            "engineered_causal_feature_gain": _difference(
                causal_value, tabular_value
            ),
            "best_graph_architecture": best_graph,
            "best_graph_value": best_graph_value,
            "graph_message_passing_gain": _difference(
                best_graph_value, causal_value
            ),
        },
        "candidates": candidates,
        "scenario_analysis": evaluate_scenarios(
            all_predictions, scenario_by_nomination_id
        ),
    }
