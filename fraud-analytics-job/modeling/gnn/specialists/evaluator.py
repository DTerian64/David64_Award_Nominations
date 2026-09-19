"""Nested temporal evaluation for independently admitted GNN specialists."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable

import numpy as np

from ..evaluators.graph_value_by_ablation.feature_profiles import FULL_CAUSAL
from ..evaluators.graph_value_by_ablation.rolling_folds import (
    evaluate_candidate_over_folds,
)
from .contracts import SpecialistTrackPolicy
from .feature_contracts import apply_specialist_feature_contract

logger = logging.getLogger(__name__)

EVALUATOR_VERSION = "specialist-nested-temporal-v1"
BASELINE = "causal_mlp_baseline"


def _filter_target(target: dict, label_map: dict[int, int]) -> tuple[dict, np.ndarray]:
    keep = [
        index for index, nomination_id in enumerate(target["nom_ids"])
        if int(nomination_id) in label_map
    ]
    filtered = dict(target)
    filtered["nom_ids"] = [target["nom_ids"][index] for index in keep]
    filtered["x"] = target["x"][keep]
    filtered["pairs"] = target["pairs"][keep]
    labels = np.asarray(
        [label_map[int(nomination_id)] for nomination_id in filtered["nom_ids"]],
        dtype=np.int64,
    )
    return filtered, labels


def specialist_fold_views(
    folds: list[dict],
    label_map: dict[int, int],
    feature_contract: str,
) -> tuple[list[dict], list[np.ndarray], list[np.ndarray]]:
    """Create filtered fold views without mutating the shared v2 population."""
    views: list[dict] = []
    train_labels: list[np.ndarray] = []
    eval_labels: list[np.ndarray] = []
    for source in folds:
        view = dict(source)
        view["train"], y_train = _filter_target(source["train"], label_map)
        view["eval"], y_eval = _filter_target(source["eval"], label_map)
        view = apply_specialist_feature_contract(view, feature_contract)
        views.append(view)
        train_labels.append(y_train)
        eval_labels.append(y_eval)
    return views, train_labels, eval_labels


def _population(labels: Iterable[np.ndarray]) -> dict[str, int]:
    arrays = [np.asarray(value, dtype=np.int64) for value in labels]
    combined = np.concatenate(arrays) if arrays else np.asarray([], dtype=np.int64)
    positives = int(combined.sum())
    return {
        "count": int(len(combined)),
        "positive_count": positives,
        "negative_count": int(len(combined) - positives),
    }


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _candidate_summary(result: dict) -> dict:
    folds = result.get("folds") or []
    selection = folds[:-1]
    values = [
        value for value in (_finite(row.get("pr_auc")) for row in selection)
        if value is not None
    ]
    final = folds[-1] if folds else {}
    return {
        **result,
        "selection_fold_count": len(values),
        "selection_macro_pr_auc": float(np.mean(values)) if values else None,
        "selection_pr_auc_range": (
            float(max(values) - min(values)) if values else None
        ),
        "final_holdout": final,
    }


def _platt_calibration(predictions: list[dict]) -> dict:
    """Fit a small deterministic Platt map over pre-holdout predictions."""
    if not predictions:
        return {"method": "IDENTITY", "slope": 1.0, "intercept": 0.0}
    final_fold = max(int(row["fold_index"]) for row in predictions)
    rows = [row for row in predictions if int(row["fold_index"]) != final_fold]
    labels = np.asarray([int(row["label"]) for row in rows], dtype=float)
    probabilities = np.asarray(
        [float(row["probability"]) for row in rows], dtype=float
    )
    if not len(labels) or labels.sum() in {0, len(labels)}:
        return {"method": "IDENTITY", "slope": 1.0, "intercept": 0.0}
    clipped = np.clip(probabilities, 1e-6, 1.0 - 1e-6)
    scores = np.log(clipped / (1.0 - clipped))
    design = np.column_stack([scores, np.ones(len(scores))])
    coefficients = np.zeros(2, dtype=float)
    for _ in range(50):
        fitted = 1.0 / (1.0 + np.exp(-np.clip(design @ coefficients, -60, 60)))
        weights = np.maximum(fitted * (1.0 - fitted), 1e-6)
        gradient = design.T @ (fitted - labels)
        hessian = design.T @ (design * weights[:, None])
        hessian[0, 0] += 1e-6
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            return {"method": "IDENTITY", "slope": 1.0, "intercept": 0.0}
        coefficients -= step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return {
        "method": "PLATT",
        "slope": float(coefficients[0]),
        "intercept": float(coefficients[1]),
        "fitted_count": int(len(labels)),
        "positive_count": int(labels.sum()),
    }


def _calibrated_holdout_brier(
    predictions: list[dict], calibration: dict
) -> float | None:
    if not predictions:
        return None
    final_fold = max(int(row["fold_index"]) for row in predictions)
    rows = [row for row in predictions if int(row["fold_index"]) == final_fold]
    if not rows:
        return None
    raw = np.clip(
        np.asarray([float(row["probability"]) for row in rows]),
        1e-6,
        1.0 - 1e-6,
    )
    logits = np.log(raw / (1.0 - raw))
    calibrated_logits = (
        float(calibration.get("slope", 1.0)) * logits
        + float(calibration.get("intercept", 0.0))
    )
    probabilities = 1.0 / (
        1.0 + np.exp(-np.clip(calibrated_logits, -60, 60))
    )
    labels = np.asarray([int(row["label"]) for row in rows], dtype=float)
    return float(np.mean(np.square(probabilities - labels)))


def evaluate_specialist(
    *,
    track: SpecialistTrackPolicy,
    folds: list[dict],
    label_map: dict[int, int],
    hidden_dim: int,
    emb_dim: int,
    epochs: int,
    candidate_evaluator: Callable = evaluate_candidate_over_folds,
    incumbent_architecture: str | None = None,
) -> dict:
    """Evaluate one behavior track without changing a serving pointer."""
    base = {
        "track": track.key,
        "feature_contract": track.feature_contract,
        "evaluator": EVALUATOR_VERSION,
        "role": "SPECIALIST_ADMISSION",
        "affects_serving_selection": True,
    }
    if not track.enabled:
        return {**base, "status": "DISABLED", "reason": "SPECIALIST_DISABLED"}
    if len(folds) < 3:
        return {
            **base,
            "status": "NOT_ADMITTED",
            "reason": "INSUFFICIENT_TEMPORAL_COVERAGE",
        }

    views, y_train, y_eval = specialist_fold_views(
        folds, label_map, track.feature_contract
    )
    training = _population(y_train)
    final_holdout = _population([y_eval[-1]])
    evaluable_selection_folds = sum(
        int(values.sum()) > 0 and int(values.sum()) < len(values)
        for values in y_eval[:-1]
    )
    populations = {
        "training": training,
        "final_holdout": final_holdout,
        "evaluable_selection_folds": evaluable_selection_folds,
    }
    gates = (
        training["positive_count"] >= track.minimum_train_positives,
        training["negative_count"] >= track.minimum_train_negatives,
        final_holdout["positive_count"] >= track.minimum_holdout_positives,
        final_holdout["negative_count"] >= track.minimum_holdout_negatives,
        evaluable_selection_folds >= track.minimum_evaluable_temporal_folds,
    )
    if not all(gates):
        return {
            **base,
            "status": "NOT_ADMITTED",
            "reason": "INSUFFICIENT_SPECIALIST_LABELS",
            "populations": populations,
        }

    candidates: dict[str, dict] = {}
    specs = ((BASELINE, "mlp"), *(
        (architecture, architecture)
        for architecture in track.candidate_architectures
    ))
    for candidate_name, architecture in specs:
        logger.info(
            "GNN specialist candidate starting: %s / %s",
            track.key,
            candidate_name,
        )
        try:
            result, predictions = candidate_evaluator(
                candidate_name=candidate_name,
                architecture=architecture,
                feature_profile=FULL_CAUSAL,
                folds=views,
                y_train_by_fold=y_train,
                y_eval_by_fold=y_eval,
                hidden_dim=hidden_dim,
                emb_dim=emb_dim,
                epochs=epochs,
            )
            calibration = _platt_calibration(predictions)
            summary = _candidate_summary(result)
            calibrated_brier = _calibrated_holdout_brier(
                predictions, calibration
            )
            if calibrated_brier is not None:
                summary["final_holdout"] = {
                    **summary["final_holdout"],
                    "calibrated_brier_score": calibrated_brier,
                }
            candidates[candidate_name] = {
                **summary,
                "calibration": calibration,
            }
        except Exception as exc:
            logger.exception(
                "GNN specialist candidate failed: %s / %s",
                track.key,
                candidate_name,
            )
            candidates[candidate_name] = {
                "status": "FAILED",
                "architecture": architecture,
                "reason": type(exc).__name__,
                "detail": str(exc)[:500],
            }

    baseline = candidates.get(BASELINE, {})
    baseline_selection = _finite(baseline.get("selection_macro_pr_auc"))
    baseline_final = _finite(
        (baseline.get("final_holdout") or {}).get("pr_auc")
    )
    eligible = {}
    for architecture in track.candidate_architectures:
        row = candidates.get(architecture, {})
        value = _finite(row.get("selection_macro_pr_auc"))
        spread = _finite(row.get("selection_pr_auc_range"))
        if (
            row.get("status") == "COMPLETED"
            and value is not None
            and int(row.get("selection_fold_count") or 0)
                >= track.minimum_evaluable_temporal_folds
            and spread is not None
            and spread <= track.maximum_fold_pr_auc_range
        ):
            eligible[architecture] = row

    if baseline_selection is None or not eligible:
        return {
            **base,
            "status": "NOT_ADMITTED",
            "reason": "SPECIALIST_GUARDRAIL_FAILED",
            "populations": populations,
            "candidates": candidates,
        }

    provisional = max(
        eligible,
        key=lambda name: (eligible[name]["selection_macro_pr_auc"], name),
    )
    selection_reason = "HIGHEST_SELECTION_FOLD_PR_AUC"
    if incumbent_architecture in eligible and incumbent_architecture != provisional:
        challenger = float(eligible[provisional]["selection_macro_pr_auc"])
        incumbent_value = float(
            eligible[incumbent_architecture]["selection_macro_pr_auc"]
        )
        if challenger - incumbent_value <= track.architecture_switch_tolerance:
            provisional = incumbent_architecture
            selection_reason = "INCUMBENT_RETAINED_CHALLENGER_TIED"
    winner = eligible[provisional]
    winner_final = winner.get("final_holdout") or {}
    winner_pr_auc = _finite(winner_final.get("pr_auc"))
    winner_brier = _finite(
        winner_final.get("calibrated_brier_score", winner_final.get("brier_score"))
    )
    winner_latency = _finite(winner_final.get("inference_ms"))
    admission_failures = []
    required_margin = (
        -track.incumbent_refresh_tolerance
        if provisional == incumbent_architecture
        else track.minimum_improvement_over_mlp
    )
    if winner_pr_auc is None or baseline_final is None or (
        winner_pr_auc < baseline_final + required_margin
    ):
        admission_failures.append("NO_MESSAGE_PASSING_VALUE_OVER_MLP")
    if winner_brier is None or winner_brier > track.maximum_holdout_brier_score:
        admission_failures.append("HOLDOUT_BRIER_GUARDRAIL_FAILED")
    if (
        winner_latency is None
        or winner_latency > track.maximum_holdout_inference_ms
    ):
        admission_failures.append("HOLDOUT_LATENCY_GUARDRAIL_FAILED")

    return {
        **base,
        "status": "ADMITTED" if not admission_failures else "NOT_ADMITTED",
        "reason": (
            (
                "INCUMBENT_REFRESHED_WITHIN_TOLERANCE"
                if provisional == incumbent_architecture
                else "ADMITTED_HIGHEST_ELIGIBLE_PR_AUC"
            )
            if not admission_failures
            else admission_failures[0]
        ),
        "guardrail_failures": admission_failures,
        "populations": populations,
        "provisional_architecture": provisional,
        "architecture_selection_reason": selection_reason,
        "incumbent_architecture": incumbent_architecture,
        "selection_metric_value": winner.get("selection_macro_pr_auc"),
        "final_holdout_pr_auc": winner_pr_auc,
        "final_holdout_mlp_pr_auc": baseline_final,
        "improvement_over_mlp": (
            winner_pr_auc - baseline_final
            if winner_pr_auc is not None and baseline_final is not None
            else None
        ),
        "candidates": candidates,
    }


def evaluate_specialists(
    *,
    tracks: Iterable[SpecialistTrackPolicy],
    folds: list[dict],
    label_maps: dict[str, dict[int, int]],
    hidden_dim: int,
    emb_dim: int,
    epochs: int,
    candidate_evaluator: Callable = evaluate_candidate_over_folds,
    incumbent_specialists: dict[str, dict] | None = None,
) -> dict:
    """Evaluate every configured track independently and preserve failures."""
    results = {}
    for track in tracks:
        try:
            results[track.key] = evaluate_specialist(
                track=track,
                folds=folds,
                label_map=label_maps.get(track.key, {}),
                hidden_dim=hidden_dim,
                emb_dim=emb_dim,
                epochs=epochs,
                candidate_evaluator=candidate_evaluator,
                incumbent_architecture=(
                    (incumbent_specialists or {}).get(track.key, {}).get(
                        "architecture"
                    )
                ),
            )
        except Exception as exc:
            logger.exception("GNN specialist evaluation failed: %s", track.key)
            results[track.key] = {
                "track": track.key,
                "status": "FAILED",
                "reason": type(exc).__name__,
                "detail": str(exc)[:500],
            }
    return {
        "schema_version": 1,
        "evaluator": EVALUATOR_VERSION,
        "role": "SPECIALIST_ADMISSION",
        "affects_serving_selection": True,
        "tracks": results,
        "summary": {
            "configured": len(results),
            "admitted": sum(row.get("status") == "ADMITTED" for row in results.values()),
            "not_admitted": sum(
                row.get("status") == "NOT_ADMITTED" for row in results.values()
            ),
            "disabled": sum(row.get("status") == "DISABLED" for row in results.values()),
            "failed": sum(row.get("status") == "FAILED" for row in results.values()),
        },
    }
