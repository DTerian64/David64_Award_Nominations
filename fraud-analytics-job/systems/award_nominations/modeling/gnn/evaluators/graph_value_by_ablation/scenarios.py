"""Scenario-level diagnostics backed by explicit adjudication metadata."""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from ..metrics import binary_metrics


def evaluate_scenarios(
    predictions: list[dict],
    scenario_by_nomination_id: dict[int, str],
) -> dict:
    """Compare each fraud scenario with labelled legitimate observations.

    Synthetic scenario families are read from persisted ground-truth metadata;
    this evaluator never guesses a scenario from text or another engine's output.
    """
    if not scenario_by_nomination_id:
        return {
            "status": "UNAVAILABLE",
            "reason": "SCENARIO_METADATA_NOT_AVAILABLE",
            "candidates": {},
        }

    by_candidate: dict[str, list[dict]] = defaultdict(list)
    for row in predictions:
        family = scenario_by_nomination_id.get(int(row["nomination_id"]))
        if family:
            by_candidate[str(row["candidate"])].append({**row, "scenario": family})

    candidate_results: dict[str, dict] = {}
    for candidate, rows in by_candidate.items():
        legitimate = [row for row in rows if int(row["label"]) == 0]
        fraud_families = sorted({
            str(row["scenario"])
            for row in rows
            if int(row["label"]) == 1
        })
        scenarios: dict[str, dict] = {}
        for family in fraud_families:
            positives = [
                row for row in rows
                if int(row["label"]) == 1 and row["scenario"] == family
            ]
            population = [*positives, *legitimate]
            labels = np.asarray([row["label"] for row in population], dtype=np.int64)
            probabilities = np.asarray(
                [row["probability"] for row in population], dtype=float
            )
            # logit(probability) is monotonic and keeps the shared metric helper
            # independent of whether a caller retained raw logits.
            clipped = np.clip(probabilities, 1e-12, 1 - 1e-12)
            logits = np.log(clipped / (1.0 - clipped))
            positive_probabilities = np.asarray(
                [row["probability"] for row in positives], dtype=float
            )
            scenarios[family] = {
                **binary_metrics(labels, logits),
                "fraud_example_count": len(positives),
                "legitimate_comparison_count": len(legitimate),
                "mean_fraud_probability": (
                    float(np.mean(positive_probabilities))
                    if len(positive_probabilities) else None
                ),
                "median_fraud_probability": (
                    float(np.median(positive_probabilities))
                    if len(positive_probabilities) else None
                ),
            }
        candidate_results[candidate] = {
            "scenario_count": len(scenarios),
            "labelled_prediction_count": len(rows),
            "scenarios": scenarios,
        }

    return {
        "status": "COMPLETED" if candidate_results else "UNAVAILABLE",
        "reason": None if candidate_results else "NO_SCENARIO_ROWS_IN_EVALUATION_FOLDS",
        "candidates": candidate_results,
    }
