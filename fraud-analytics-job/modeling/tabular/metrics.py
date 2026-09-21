"""Common holdout metrics for fair Tabular candidate comparison."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


def probability_metrics(target: pd.Series, probabilities: np.ndarray) -> dict:
    values = np.asarray(probabilities, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Tabular candidate emitted non-finite probabilities")
    labels = target.to_numpy(dtype=int)
    base_rate = float(labels.mean())
    pr_auc = float(average_precision_score(labels, values))
    return {
        "selection_metric": "holdout_pr_auc",
        "holdout_pr_auc": pr_auc,
        "holdout_roc_auc": float(roc_auc_score(labels, values)),
        "holdout_brier_score": float(brier_score_loss(labels, values)),
        "holdout_base_rate": base_rate,
        "holdout_pr_auc_lift": pr_auc / base_rate if base_rate else None,
    }


def holdout_permutation_importance(
    model: object,
    evaluation_matrix: np.ndarray,
    target: pd.Series,
    feature_names: tuple[str, ...],
    *,
    repeats: int,
    random_seed: int,
) -> dict:
    """Measure each feature by its mean PR-AUC loss on the shared holdout."""

    matrix = np.asarray(evaluation_matrix, dtype=float)
    labels = target.to_numpy(dtype=int)
    if matrix.ndim != 2 or matrix.shape[1] != len(feature_names):
        raise ValueError("Permutation importance feature dimensions do not match")
    if matrix.shape[0] != len(labels):
        raise ValueError("Permutation importance holdout rows do not match labels")

    result = permutation_importance(
        model,
        matrix,
        labels,
        scoring="average_precision",
        n_repeats=repeats,
        random_state=random_seed,
        n_jobs=1,
    )
    features = sorted(
        (
            {
                "name": str(name),
                "pr_auc_decrease_mean": float(mean),
                "pr_auc_decrease_std": float(std),
            }
            for name, mean, std in zip(
                feature_names,
                result.importances_mean,
                result.importances_std,
            )
        ),
        key=lambda item: (-item["pr_auc_decrease_mean"], item["name"]),
    )
    return {
        "schema_version": 1,
        "method": "PERMUTATION_IMPORTANCE",
        "scoring": "average_precision",
        "score_semantics": "decrease_in_holdout_pr_auc",
        "holdout_row_count": int(matrix.shape[0]),
        "repeats": repeats,
        "random_seed": random_seed,
        "features": features,
    }
