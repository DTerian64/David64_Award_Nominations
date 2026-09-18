"""Fair evaluation and deterministic selection of Tabular candidates."""

from __future__ import annotations

import math

from feature_builders import TabularFeatureDataset

from .contracts import (
    TabularCandidateEvaluation,
    TabularCandidateResult,
    TabularSelectionPolicy,
    TabularSelectionResult,
    TabularTrainingPolicy,
)
from .tabular_mlp import train_tabular_mlp_candidate
from .random_forest import train_random_forest_candidate
from .training_data import prepare_tabular_holdout


def _candidate_summary(
    candidate: TabularCandidateResult,
    policy: TabularSelectionPolicy,
) -> dict:
    failures: list[str] = []
    metric = candidate.metrics.get(policy.selection_metric)
    lift = candidate.metrics.get("holdout_pr_auc_lift")
    if not candidate.completed:
        failures.append(candidate.reason_code or "CANDIDATE_NOT_COMPLETED")
    if metric is None or not math.isfinite(float(metric)):
        failures.append("SELECTION_METRIC_UNAVAILABLE")
    if lift is None or float(lift) < policy.minimum_pr_auc_lift:
        failures.append("PR_AUC_LIFT_BELOW_MINIMUM")
    return {
        "status": candidate.status,
        "eligible": not failures,
        "guardrail_failures": failures,
        "metrics": dict(candidate.metrics),
        "feature_schema_id": candidate.feature_schema_id,
        "reason_code": candidate.reason_code,
        "reason_detail": candidate.reason_detail,
    }


def evaluate_tabular_candidates(
    dataset: TabularFeatureDataset,
    training_policy: TabularTrainingPolicy | None = None,
    selection_policy: TabularSelectionPolicy | None = None,
) -> TabularCandidateEvaluation:
    """Train both candidates on one prepared holdout and select one winner."""

    training_policy = training_policy or TabularTrainingPolicy()
    selection_policy = selection_policy or TabularSelectionPolicy()
    prepared, _, _ = prepare_tabular_holdout(dataset, training_policy)
    candidates = {
        "random_forest": train_random_forest_candidate(
            dataset,
            training_policy,
            prepared=prepared,
        ),
        "tabular_mlp": train_tabular_mlp_candidate(
            dataset,
            training_policy,
            prepared=prepared,
        ),
    }
    summaries = {
        name: _candidate_summary(candidate, selection_policy)
        for name, candidate in candidates.items()
    }
    eligible = [name for name, summary in summaries.items() if summary["eligible"]]
    if not eligible:
        selection = TabularSelectionResult(
            status="NO_ELIGIBLE_CANDIDATE",
            selected_architecture=None,
            selection_metric=selection_policy.selection_metric,
            selection_reason="NO_CANDIDATE_PASSED_GUARDRAILS",
            candidate_evaluations=summaries,
        )
        return TabularCandidateEvaluation(candidates=candidates, selection=selection)

    ranked = sorted(
        eligible,
        key=lambda name: (
            -float(candidates[name].metrics[selection_policy.selection_metric]),
            name,
        ),
    )
    selected = ranked[0]
    reason = "HIGHEST_ELIGIBLE_PR_AUC"
    if len(ranked) > 1:
        difference = abs(
            float(candidates[ranked[0]].metrics[selection_policy.selection_metric])
            - float(candidates[ranked[1]].metrics[selection_policy.selection_metric])
        )
        preferred = selection_policy.preferred_architecture_on_tie
        if difference <= selection_policy.tie_tolerance and preferred in ranked[:2]:
            selected = preferred
            reason = "TIE_WITHIN_TOLERANCE_PREFERRED_ARCHITECTURE"

    selection = TabularSelectionResult(
        status="SELECTED",
        selected_architecture=selected,
        selection_metric=selection_policy.selection_metric,
        selection_reason=reason,
        candidate_evaluations=summaries,
    )
    return TabularCandidateEvaluation(candidates=candidates, selection=selection)
