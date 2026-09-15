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

