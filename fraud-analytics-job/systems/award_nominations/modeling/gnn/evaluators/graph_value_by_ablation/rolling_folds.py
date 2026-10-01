"""Leakage-safe rolling-origin diagnostics for candidate architectures."""

from __future__ import annotations

import time

import numpy as np
import torch

from ...model import fit_candidate_rolling
from ..metrics import binary_metrics, sigmoid
from .feature_profiles import apply_feature_profile


def evaluate_candidate_over_folds(
    *,
    candidate_name: str,
    architecture: str,
    feature_profile: str,
    folds: list[dict],
    y_train_by_fold: list[np.ndarray],
    y_eval_by_fold: list[np.ndarray],
    hidden_dim: int,
    emb_dim: int,
    epochs: int,
    seed: int = 42,
) -> tuple[dict, list[dict]]:
    """Train independently at every temporal origin and score its next window.

    A model trained at a later origin is never reused for an earlier fold. This
    is deliberately separate from serving selection and cannot promote a model.
    """
    if not (len(folds) == len(y_train_by_fold) == len(y_eval_by_fold)):
        raise ValueError("Each rolling fold needs train and evaluation labels")

    fold_results: list[dict] = []
    predictions: list[dict] = []
    for index, (source, y_train, y_eval) in enumerate(
        zip(folds, y_train_by_fold, y_eval_by_fold), start=1
    ):
        graph = apply_feature_profile(source, feature_profile)
        started = time.perf_counter()
        model, fit = fit_candidate_rolling(
            [graph],
            [y_train],
            architecture=architecture,
            hidden_dim=hidden_dim,
            emb_dim=emb_dim,
            epochs=epochs,
            seed=seed + index - 1,
            log_every=0,
        )
        inference_started = time.perf_counter()
        model.eval()
        with torch.no_grad():
            logits = model(
                graph["data"], graph["eval"]["pairs"], graph["eval"]["x"]
            ).numpy()
        inference_ms = (time.perf_counter() - inference_started) * 1000.0
        metrics = binary_metrics(y_eval, logits)
        fold_results.append({
            "fold_index": int(source.get("fold_index", index)),
            "graph_cutoff": source["t_graph"].isoformat(),
            "train_end": source["t_cut"].isoformat(),
            "eval_end": source["eval_end"].isoformat(),
            "feature_profile": feature_profile,
            "training_duration_seconds": time.perf_counter() - started,
            "inference_ms": inference_ms,
            "parameter_count": fit["parameter_count"],
            "train_count": int(len(y_train)),
            "train_positive_count": int(np.sum(y_train)),
            **metrics,
        })
        probabilities = sigmoid(logits)
        predictions.extend(
            {
                "candidate": candidate_name,
                "fold_index": int(source.get("fold_index", index)),
                "nomination_id": int(nomination_id),
                "label": int(label),
                "probability": float(probability),
            }
            for nomination_id, label, probability in zip(
                graph["eval"]["nom_ids"], y_eval, probabilities
            )
        )

    pr_values = [row["pr_auc"] for row in fold_results if row["pr_auc"] is not None]
    return ({
        "status": "COMPLETED",
        "architecture": architecture,
        "feature_profile": feature_profile,
        "fold_count": len(fold_results),
        "macro_pr_auc": float(np.mean(pr_values)) if pr_values else None,
        "minimum_fold_pr_auc": float(min(pr_values)) if pr_values else None,
        "maximum_fold_pr_auc": float(max(pr_values)) if pr_values else None,
        "folds": fold_results,
    }, predictions)
