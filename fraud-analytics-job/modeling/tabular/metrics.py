"""Common probability metrics for fair Tabular candidate comparison."""

from __future__ import annotations

import numpy as np
import pandas as pd
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
