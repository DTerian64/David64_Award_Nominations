"""Dependency-light binary metrics shared by GNN evaluators."""

from __future__ import annotations

import math

import numpy as np


def pr_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Return average precision, or NaN when only one class is present."""
    y_true = np.asarray(y_true).astype(int)
    if y_true.sum() == 0 or y_true.sum() == len(y_true):
        return float("nan")
    order = np.argsort(-np.asarray(y_score))
    y = y_true[order]
    true_positives = np.cumsum(y)
    precision = true_positives / np.arange(1, len(y) + 1)
    return float((precision * y).sum() / y.sum())


def roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Return rank-based ROC-AUC, or NaN when only one class is present."""
    y_true = np.asarray(y_true).astype(int)
    n_pos = int(y_true.sum())
    n_neg = int((1 - y_true).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(np.asarray(y_score))
    ranks = np.empty(len(y_true), dtype=float)
    ranks[order] = np.arange(1, len(y_true) + 1)
    return float(
        (ranks[y_true == 1].sum() - n_pos * (n_pos + 1) / 2)
        / (n_pos * n_neg)
    )


def sigmoid(logits: np.ndarray) -> np.ndarray:
    """Convert logits to probabilities without overflowing exp()."""
    values = np.asarray(logits, dtype=float)
    return 1.0 / (1.0 + np.exp(-np.clip(values, -60.0, 60.0)))


def json_number(value: float) -> float | None:
    """Return finite numeric values and represent unavailable metrics as null."""
    value = float(value)
    return value if math.isfinite(value) else None


def binary_metrics(y_true: np.ndarray, logits: np.ndarray) -> dict:
    """Compute the common metric set used by temporal and scenario reports."""
    labels = np.asarray(y_true, dtype=np.int64)
    scores = np.asarray(logits, dtype=float)
    probabilities = sigmoid(scores)
    base_rate = float(np.mean(labels)) if len(labels) else float("nan")
    average_precision = pr_auc(labels, scores)
    return {
        "pr_auc": json_number(average_precision),
        "roc_auc": json_number(roc_auc(labels, scores)),
        "base_rate": json_number(base_rate),
        "lift": json_number(
            average_precision / base_rate
            if base_rate and math.isfinite(average_precision)
            else float("nan")
        ),
        "brier_score": json_number(
            float(np.mean(np.square(probabilities - labels)))
            if len(labels)
            else float("nan")
        ),
        "count": int(len(labels)),
        "positive_count": int(labels.sum()),
        "negative_count": int(len(labels) - labels.sum()),
    }


def display_threshold_metrics(
    y_true: np.ndarray, calibrated_logits: np.ndarray, medium_threshold: float
) -> dict:
    """Measure material pattern alerts using the same score rule as inference."""
    labels = np.asarray(y_true, dtype=np.int64)
    logits = np.asarray(calibrated_logits, dtype=float)
    if labels.shape != logits.shape or labels.ndim != 1:
        raise ValueError("Display-threshold labels and logits must be matching vectors")
    if not np.isin(labels, (0, 1)).all() or not np.isfinite(logits).all():
        raise ValueError("Display-threshold inputs must be binary and finite")

    scores = np.array(
        [int(round(float(probability) * 100)) for probability in sigmoid(logits)],
        dtype=np.int64,
    )
    alerted = scores >= medium_threshold
    positive = labels == 1
    true_positive = int(np.count_nonzero(alerted & positive))
    false_positive = int(np.count_nonzero(alerted & ~positive))
    false_negative = int(np.count_nonzero(~alerted & positive))
    true_negative = int(np.count_nonzero(~alerted & ~positive))
    alert_count = true_positive + false_positive
    positive_count = true_positive + false_negative
    negative_count = false_positive + true_negative

    return {
        "minimum_risk_level": "MEDIUM",
        "threshold_score": float(medium_threshold),
        "score_derivation": "round(calibrated_probability * 100)",
        "count": int(len(labels)),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "alert_count": alert_count,
        "true_positive_count": true_positive,
        "false_positive_count": false_positive,
        "false_negative_count": false_negative,
        "true_negative_count": true_negative,
        "precision": true_positive / alert_count if alert_count else None,
        "recall": true_positive / positive_count if positive_count else None,
        "false_positive_rate": (
            false_positive / negative_count if negative_count else None
        ),
        "alert_rate": alert_count / len(labels) if len(labels) else None,
    }
