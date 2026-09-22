"""Leakage-safe temporal selection and final test for the shared GNN v4."""

from __future__ import annotations

import math
import time

import numpy as np
import torch
from sklearn.linear_model import LogisticRegression

from .evaluators.metrics import binary_metrics, sigmoid
from .shared_multi_head import (
    PatternTargets, SharedMultiHeadModel, build_pattern_targets,
    masked_joint_loss,
)
from .specialists.contracts import BEHAVIOR_TRACKS


def _targets(labelled, fold: dict, split: str) -> PatternTargets:
    return build_pattern_targets(labelled, fold[split]["nom_ids"])


def _concat_targets(parts: list[PatternTargets]) -> PatternTargets:
    return PatternTargets(
        np.concatenate([part.overall for part in parts]),
        np.concatenate([part.patterns for part in parts]),
        np.concatenate([part.mask for part in parts]),
    )


def _fit(
    folds: list[dict], labelled, architecture: str, contracts: dict[str, str],
    *, hidden_dim: int, emb_dim: int, epochs: int,
    overall_weight: float, pattern_weight: float, seed: int = 42,
) -> tuple[SharedMultiHeadModel, dict]:
    """Fixed-epoch fit; no evaluation interval is consulted for early stopping."""
    if not folds:
        raise ValueError("At least one training fold is required")
    torch.manual_seed(seed)
    np.random.seed(seed)
    model = SharedMultiHeadModel(
        architecture, folds[-1], contracts, hidden_dim=hidden_dim, emb_dim=emb_dim,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01, weight_decay=5e-4)
    targets = [_targets(labelled, fold, "train") for fold in folds]
    if any(not len(part.overall) for part in targets):
        raise ValueError("A v4 training fold has no supervised targets")
    started = time.perf_counter()
    for _ in range(epochs):
        model.train()
        optimizer.zero_grad()
        for fold, target in zip(folds, targets):
            logits = model(
                fold["data"], fold["train"]["pairs"], fold["train"]["x"]
            )
            loss = masked_joint_loss(
                logits, target, overall_weight, pattern_weight,
            )
            (loss / len(folds)).backward()
        optimizer.step()
    model.eval()
    all_targets = _concat_targets(targets)
    return model, {
        "epochs_run": epochs,
        "training_duration_seconds": time.perf_counter() - started,
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "training_count": int(len(all_targets.overall)),
        "training_positive_count": int(all_targets.overall.sum()),
        "pattern_training_positive_counts": {
            key: int(all_targets.patterns[:, j][all_targets.mask[:, j].astype(bool)].sum())
            for j, key in enumerate(BEHAVIOR_TRACKS)
        },
    }


def _score(model: SharedMultiHeadModel, fold: dict, split: str, targets: PatternTargets) -> tuple[dict, dict]:
    started = time.perf_counter()
    with torch.no_grad():
        output = model(fold["data"], fold[split]["pairs"], fold[split]["x"])
    latency_ms = (time.perf_counter() - started) * 1000
    logits = {key: value.detach().numpy() for key, value in output.items()}
    metrics = {
        "overall": binary_metrics(targets.overall, logits["OVERALL"]),
        "pattern_heads": {},
        "inference_ms": latency_ms,
    }
    for j, key in enumerate(BEHAVIOR_TRACKS):
        if key not in logits:
            continue
        known = targets.mask[:, j].astype(bool)
        metrics["pattern_heads"][key] = binary_metrics(
            targets.patterns[:, j][known], logits[key][known]
        )
    values = [
        row["pr_auc"] for row in metrics["pattern_heads"].values()
        if row["pr_auc"] is not None
    ]
    metrics["macro_pattern_pr_auc"] = float(np.mean(values)) if values else None
    return metrics, logits


def select_shared_architecture(candidates: dict[str, dict], tolerance: float) -> dict:
    """Select using validation folds only; final-test data is not an argument."""
    eligible = {
        architecture: row for architecture, row in candidates.items()
        if row.get("status") == "COMPLETED"
        and row.get("validation_overall_pr_auc") is not None
    }
    if not eligible:
        return {"selected_architecture": None, "reason": "NO_VALIDATION_CANDIDATE"}
    best = max(row["validation_overall_pr_auc"] for row in eligible.values())
    contenders = [
        (key, row) for key, row in eligible.items()
        if best - row["validation_overall_pr_auc"] <= tolerance
    ]
    contenders.sort(key=lambda item: (
        -(item[1].get("validation_macro_pattern_pr_auc") or -1),
        item[1].get("validation_inference_ms", math.inf),
        item[0],
    ))
    winner = contenders[0][0]
    return {
        "selected_architecture": winner,
        "reason": "HIGHEST_VALIDATION_OVERALL_PR_AUC_WITH_PATTERN_TIE_BREAK",
        "validation_overall_pr_auc": eligible[winner]["validation_overall_pr_auc"],
        "validation_macro_pattern_pr_auc": eligible[winner].get(
            "validation_macro_pattern_pr_auc"
        ),
        "overall_tie_tolerance": tolerance,
    }


def _fit_validation_calibration(samples: list[tuple[np.ndarray, np.ndarray]]) -> dict | None:
    """Fit Platt scaling only from out-of-time architecture-validation rows."""
    if not samples:
        return None
    logits = np.concatenate([item[0] for item in samples])
    labels = np.concatenate([item[1] for item in samples]).astype(int)
    if len(logits) < 20 or labels.sum() < 5 or labels.sum() == len(labels):
        return None
    model = LogisticRegression(max_iter=1000, random_state=42)
    model.fit(logits.reshape(-1, 1), labels)
    slope = float(model.coef_[0, 0])
    if not math.isfinite(slope) or slope <= 0:
        return None
    return {
        "method": "temporal_validation_platt",
        "slope": slope,
        "intercept": float(model.intercept_[0]),
        "validation_count": int(len(labels)),
        "validation_positive_count": int(labels.sum()),
    }


def _calibrated_metrics(raw_logits: np.ndarray, labels: np.ndarray, calibration: dict | None) -> dict:
    if calibration is None:
        return binary_metrics(labels, raw_logits)
    scaled = calibration["slope"] * raw_logits + calibration["intercept"]
    return binary_metrics(labels, scaled)


def evaluate_shared_model(folds: list[dict], labelled, policy) -> tuple[dict, SharedMultiHeadModel | None]:
    """Validation-select once, then evaluate the frozen winner on final time."""
    if len(folds) < 3:
        raise ValueError("V4 requires two validation periods and a final temporal test")
    contracts = {key: contract for key, contract, enabled in policy.pattern_heads if enabled}
    fit_options = dict(
        hidden_dim=policy.hidden_dim, emb_dim=policy.embed_dim, epochs=policy.epochs,
        overall_weight=policy.overall_loss_weight,
        pattern_weight=policy.pattern_total_loss_weight,
    )
    candidates = {}
    validation_samples: dict[str, dict[str, list[tuple[np.ndarray, np.ndarray]]]] = {}
    # The final fold is withheld completely until this loop has selected an
    # architecture. Both candidate fits and tie-breaks use validation only.
    for architecture in policy.candidate_architectures:
        periods = []
        samples: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
        try:
            for index in range(len(folds) - 1):
                model, fit = _fit(
                    folds[:index + 1], labelled, architecture, contracts,
                    **fit_options,
                )
                validation_targets = _targets(labelled, folds[index], "eval")
                metrics, logits = _score(
                    model, folds[index], "eval", validation_targets
                )
                samples.setdefault("OVERALL", []).append(
                    (logits["OVERALL"], validation_targets.overall)
                )
                for j, key in enumerate(BEHAVIOR_TRACKS):
                    if key in logits:
                        known = validation_targets.mask[:, j].astype(bool)
                        samples.setdefault(key, []).append((
                            logits[key][known], validation_targets.patterns[:, j][known]
                        ))
                periods.append({"fold": index + 1, "fit": fit, **metrics})
            overall = [row["overall"]["pr_auc"] for row in periods]
            macro = [row["macro_pattern_pr_auc"] for row in periods]
            candidates[architecture] = {
                "status": "COMPLETED",
                "validation_folds": periods,
                "validation_overall_pr_auc": (
                    float(np.mean(overall)) if all(value is not None for value in overall) else None
                ),
                "validation_macro_pattern_pr_auc": (
                    float(np.mean(macro)) if all(value is not None for value in macro) else None
                ),
                "validation_inference_ms": float(np.mean([row["inference_ms"] for row in periods])),
            }
            validation_samples[architecture] = samples
        except Exception as exc:
            candidates[architecture] = {
                "status": "FAILED", "reason": type(exc).__name__, "detail": str(exc)[:500]
            }
    selection = select_shared_architecture(candidates, policy.overall_tie_tolerance)
    selected = selection["selected_architecture"]
    report = {
        "schema_version": 4,
        "evaluator": "shared-multi-head-temporal-v1",
        "validation_candidates": candidates,
        "selection": selection,
        "final_test": None,
    }
    if selected is None:
        return report, None

    calibration = {
        key: _fit_validation_calibration(samples)
        for key, samples in validation_samples[selected].items()
    }
    report["calibration"] = calibration

    final = folds[-1]
    final_targets = _targets(labelled, final, "eval")
    final_models = {}
    final_metrics = {}
    # Baselines use the identical training folds and final-test population.
    for architecture in ("raw_feature_mlp", "engineered_graph_mlp", selected):
        model, fit = _fit(folds, labelled, architecture, contracts, **fit_options)
        metrics, logits = _score(model, final, "eval", final_targets)
        if architecture == selected:
            metrics["overall"] = _calibrated_metrics(
                logits["OVERALL"], final_targets.overall, calibration.get("OVERALL")
            )
            for j, key in enumerate(BEHAVIOR_TRACKS):
                if key in logits:
                    known = final_targets.mask[:, j].astype(bool)
                    metrics["pattern_heads"][key] = _calibrated_metrics(
                        logits[key][known], final_targets.patterns[:, j][known],
                        calibration.get(key),
                    )
        final_models[architecture] = model
        final_metrics[architecture] = {"fit": fit, **metrics}
    selected_pr = final_metrics[selected]["overall"]["pr_auc"]
    raw_pr = final_metrics["raw_feature_mlp"]["overall"]["pr_auc"]
    engineered_pr = final_metrics["engineered_graph_mlp"]["overall"]["pr_auc"]
    overall_admitted = (
        all(value is not None for value in (selected_pr, raw_pr, engineered_pr))
        and calibration.get("OVERALL") is not None
        and final_metrics[selected]["overall"]["brier_score"] is not None
        and final_metrics[selected]["overall"]["brier_score"] <= policy.maximum_overall_holdout_brier_score
        and final_metrics[selected]["inference_ms"] <= policy.maximum_holdout_inference_ms
        and selected_pr >= raw_pr + policy.minimum_graph_value_over_raw_mlp
        and selected_pr >= engineered_pr + policy.minimum_message_passing_value_over_engineered_graph_mlp
    )
    head_states = {}
    for key, _contract, enabled in policy.pattern_heads:
        if not enabled:
            head_states[key] = {"state": "DISABLED"}
            continue
        result = final_metrics[selected]["pattern_heads"].get(key)
        baseline = final_metrics["engineered_graph_mlp"]["pattern_heads"].get(key)
        j = BEHAVIOR_TRACKS.index(key)
        training = _concat_targets([_targets(labelled, fold, "train") for fold in folds])
        positives = int(training.patterns[:, j][training.mask[:, j].astype(bool)].sum())
        validation = [
            row["pattern_heads"].get(key, {})
            for row in candidates[selected]["validation_folds"]
        ]
        validation_pr = [row.get("pr_auc") for row in validation]
        temporal_variation_ok = (
            all(value is not None for value in validation_pr)
            and max(validation_pr) - min(validation_pr)
            <= policy.maximum_pattern_validation_pr_auc_range
        )
        enough = (
            positives >= policy.minimum_pattern_train_positives
            and result["positive_count"] >= policy.minimum_pattern_holdout_positives
            and result["negative_count"] >= policy.minimum_pattern_holdout_negatives
            and all(row.get("positive_count", 0) >= policy.minimum_pattern_validation_positives for row in validation)
        )
        pr_value = result["pr_auc"]
        base_pr = baseline["pr_auc"]
        good = (
            enough and pr_value is not None and base_pr is not None
            and calibration.get(key) is not None
            and temporal_variation_ok
            and pr_value > result["base_rate"]
            and pr_value >= base_pr + policy.minimum_pattern_improvement_over_engineered_mlp
            and result["brier_score"] is not None
            and result["brier_score"] <= policy.maximum_pattern_holdout_brier_score
        )
        head_states[key] = {
            "state": "ACTIVE" if good and overall_admitted else (
                "INSUFFICIENT_LABELS" if not enough else "DIAGNOSTIC_ONLY"
            ),
            "training_positive_count": positives,
            "final_test": result,
            "engineered_graph_mlp_pr_auc": base_pr,
        }
    report["final_test"] = {
        "selected_architecture": selected,
        "models": final_metrics,
        "graph_value_over_raw_mlp": (
            selected_pr - raw_pr if selected_pr is not None and raw_pr is not None else None
        ),
        "message_passing_value_over_engineered_graph_mlp": (
            selected_pr - engineered_pr
            if selected_pr is not None and engineered_pr is not None else None
        ),
        "admitted": overall_admitted,
        "head_states": head_states,
        "calibration": calibration,
    }
    return report, final_models[selected]
