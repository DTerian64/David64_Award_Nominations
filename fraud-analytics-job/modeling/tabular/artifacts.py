"""Immutable candidate and serving artifacts for the Tabular model family."""

from __future__ import annotations

import json
import pickle
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from feature_builders import TabularFeatureDataset

from ..artifact_manifest import artifact_descriptor, write_manifest
from .contracts import TabularCandidateEvaluation, TabularTrainingPolicy
from .serving import TabularServingFit


def _model_payload(
    *,
    tenant_id: int,
    model_version: str,
    architecture: str,
    feature_dataset: TabularFeatureDataset,
    model: Any,
    preprocessor: Any,
    embed_model_name: str,
    training_lineage: dict[str, Any],
) -> dict[str, Any]:
    amounts = feature_dataset.features["Amount"]
    category_encoder = preprocessor.category_encoder
    return {
        "schema_version": 1,
        "artifact_type": "tabular_integrity_model",
        "tenant_id": tenant_id,
        "model_version": model_version,
        "architecture": architecture,
        "feature_schema_id": feature_dataset.schema.schema_id,
        "feature_columns": list(feature_dataset.schema.feature_columns),
        "source_snapshot_id": feature_dataset.source_snapshot_id,
        "training_lineage": training_lineage,
        "model": model,
        "preprocessing": {
            "feature_columns": list(preprocessor.feature_columns),
            "imputer": preprocessor.imputer,
            "scaler": preprocessor.scaler,
            "category_fraud_rate": dict(category_encoder.category_rates),
            "global_fraud_rate": float(category_encoder.global_rate),
        },
        "amount_mean": float(amounts.mean()),
        "amount_std": float(amounts.std()),
        "embed_model_name": embed_model_name,
    }


def _model_statistics(model: Any, architecture: str) -> dict[str, Any]:
    """Return architecture-specific fitted-size diagnostics."""

    if architecture == "random_forest":
        trees = list(model.estimators_)
        depths = [int(tree.tree_.max_depth) for tree in trees]
        return {
            "model_family": "TREE_ENSEMBLE",
            "tree_count": len(trees),
            "total_node_count": int(
                sum(tree.tree_.node_count for tree in trees)
            ),
            "total_leaf_count": int(
                sum(tree.tree_.n_leaves for tree in trees)
            ),
            "maximum_observed_depth": max(depths, default=0),
            "mean_observed_depth": (
                float(sum(depths) / len(depths)) if depths else 0.0
            ),
        }
    if architecture == "tabular_mlp":
        hidden_layers = model.hidden_layer_sizes
        if isinstance(hidden_layers, int):
            hidden_layers = (hidden_layers,)
        return {
            "model_family": "FEED_FORWARD_NEURAL_NETWORK",
            "hidden_layer_shape": list(hidden_layers),
            "parameter_count": int(
                sum(weights.size for weights in model.coefs_)
                + sum(bias.size for bias in model.intercepts_)
            ),
            "iterations_run": int(model.n_iter_),
        }
    raise ValueError(f"Unsupported Tabular architecture: {architecture}")


def _write_pickle(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        pickle.dump(payload, stream)


def _write_metrics(path: Path, metrics: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )


def _write_score_distribution(
    path: Path,
    probabilities: tuple[float, ...],
    targets: tuple[int, ...],
    title: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    legitimate = [score * 100 for score, target in zip(probabilities, targets) if not target]
    fraud = [score * 100 for score, target in zip(probabilities, targets) if target]
    figure, axis = plt.subplots(figsize=(9, 5))
    axis.hist([legitimate, fraud], bins=30, label=["Legitimate", "Fraud"], alpha=0.7)
    axis.set_xlabel("Integrity risk score (0-100)")
    axis.set_ylabel("Out-of-time evaluation nominations")
    axis.set_title(title)
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(figure)


def write_tabular_bundle(
    *,
    output_dir: Path,
    tenant_id: int,
    tenant_name: str,
    model_version: str,
    feature_dataset: TabularFeatureDataset,
    evaluation: TabularCandidateEvaluation,
    serving_fit: TabularServingFit,
    training_policy: TabularTrainingPolicy,
    embed_model_name: str,
) -> tuple[Path, list[tuple[Path, str]]]:
    """Write one complete immutable run and return its upload inventory."""

    bundle_dir = output_dir / "tabular" / f"tenant_{tenant_id}" / model_version
    artifacts: list[tuple[Path, str]] = []
    candidate_manifest: dict[str, Any] = {}
    for architecture, candidate in evaluation.candidates.items():
        candidate_dir = bundle_dir / "candidates" / architecture
        model_path = candidate_dir / "model.pkl"
        metrics_path = candidate_dir / "metrics.json"
        chart_path = candidate_dir / "score_distribution.png"
        candidate_training_rows = int(candidate.metrics["training_rows"])
        candidate_fraud_rows = int(candidate.metrics["training_fraud_rows"])
        candidate_evaluation_rows = int(candidate.metrics["evaluation_rows"])
        candidate_evaluation_fraud_rows = int(
            candidate.metrics["evaluation_fraud_rows"]
        )
        candidate_statistics = _model_statistics(
            candidate.model, architecture
        )
        candidate_lineage = {
            "fit_scope": "TEMPORAL_TRAINING_PARTITION",
            "training_rows": candidate_training_rows,
            "fraud_rows": candidate_fraud_rows,
            "legitimate_rows": candidate_training_rows - candidate_fraud_rows,
            "evaluation_rows": candidate_evaluation_rows,
            "evaluation_fraud_rows": candidate_evaluation_fraud_rows,
            "evaluation_legitimate_rows": (
                candidate_evaluation_rows - candidate_evaluation_fraud_rows
            ),
            "evaluation_status": "OUT_OF_TIME_HOLDOUT",
            "model_statistics": candidate_statistics,
        }
        _write_pickle(
            model_path,
            _model_payload(
                tenant_id=tenant_id,
                model_version=model_version,
                architecture=architecture,
                feature_dataset=feature_dataset,
                model=candidate.model,
                preprocessor=candidate.preprocessor,
                embed_model_name=embed_model_name,
                training_lineage=candidate_lineage,
            ),
        )
        metrics = dict(candidate.metrics)
        metrics.update(
            evaluation.selection.candidate_evaluations.get(architecture, {})
        )
        _write_metrics(metrics_path, metrics)
        _write_score_distribution(
            chart_path,
            candidate.evaluation_probabilities,
            candidate.evaluation_targets,
            f"{tenant_name} - {architecture} out-of-time score distribution",
        )
        artifacts.extend(
            [
                (model_path, f"candidate_{architecture}_model"),
                (metrics_path, f"candidate_{architecture}_metrics"),
                (chart_path, f"candidate_{architecture}_visualization"),
            ]
        )
        manifest_metrics = {
            key: value
            for key, value in candidate.metrics.items()
            if key != "permutation_importance"
        }
        candidate_manifest[architecture] = {
            "status": candidate.status,
            "eligible": evaluation.selection.candidate_evaluations[architecture][
                "eligible"
            ],
            "metrics": manifest_metrics,
            "permutation_importance": candidate.metrics.get(
                "permutation_importance"
            ),
            "fit": candidate_lineage,
            "artifact_prefix": f"candidates/{architecture}",
        }
        if architecture == "random_forest" and hasattr(
            candidate.model, "feature_importances_"
        ):
            candidate_manifest[architecture]["feature_importance"] = sorted(
                (
                    {
                        "name": str(name),
                        "importance": float(importance),
                    }
                    for name, importance in zip(
                        candidate.preprocessor.feature_columns,
                        candidate.model.feature_importances_,
                    )
                ),
                key=lambda item: (-item["importance"], item["name"]),
            )

    serving_dir = bundle_dir / "serving"
    evaluation_dir = bundle_dir / "evaluation"
    serving_model_path = serving_dir / "model.pkl"
    selected_candidate_chart_path = (
        evaluation_dir / "selected_candidate_score_distribution.png"
    )
    serving_statistics = _model_statistics(
        serving_fit.model, serving_fit.architecture
    )
    serving_lineage = {
        "fit_scope": "ALL_MATURED_LABELS",
        "selected_from_candidate": serving_fit.architecture,
        "selection_evidence_model_path": (
            f"candidates/{serving_fit.architecture}/model.pkl"
        ),
        "selection_evidence_metrics_path": (
            f"candidates/{serving_fit.architecture}/metrics.json"
        ),
        "training_rows": serving_fit.training_rows,
        "fraud_rows": serving_fit.training_fraud_rows,
        "legitimate_rows": (
            serving_fit.training_rows - serving_fit.training_fraud_rows
        ),
        "evaluation_status": "NOT_APPLICABLE_AFTER_FULL_REFIT",
        "model_statistics": serving_statistics,
    }
    _write_pickle(
        serving_model_path,
        _model_payload(
            tenant_id=tenant_id,
            model_version=model_version,
            architecture=serving_fit.architecture,
            feature_dataset=feature_dataset,
            model=serving_fit.model,
            preprocessor=serving_fit.preprocessor,
            embed_model_name=embed_model_name,
            training_lineage=serving_lineage,
        ),
    )
    selected_candidate = evaluation.candidates[serving_fit.architecture]
    _write_score_distribution(
        selected_candidate_chart_path,
        selected_candidate.evaluation_probabilities,
        selected_candidate.evaluation_targets,
        f"{tenant_name} - selected {serving_fit.architecture} evaluation distribution",
    )
    artifacts.extend(
        [
            (serving_model_path, "serving_model"),
            (
                selected_candidate_chart_path,
                "selected_candidate_evaluation_visualization",
            ),
        ]
    )

    manifest_path = bundle_dir / "manifest.json"
    manifest = {
        "schema_version": 1,
        "artifact_type": "tabular_integrity_model",
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "model_version": model_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_snapshot_id": feature_dataset.source_snapshot_id,
        "feature_schema_id": feature_dataset.schema.schema_id,
        "feature_columns": list(feature_dataset.schema.feature_columns),
        "training_policy": asdict(training_policy),
        "selection": {
            "status": evaluation.selection.status,
            "selected_architecture": evaluation.selection.selected_architecture,
            "selection_metric": evaluation.selection.selection_metric,
            "selection_reason": evaluation.selection.selection_reason,
        },
        "candidates": candidate_manifest,
        "serving": {
            "architecture": serving_fit.architecture,
            "model_path": "serving/model.pkl",
            "fit": serving_lineage,
            "refit_training_rows": serving_fit.training_rows,
            "refit_fraud_rows": serving_fit.training_fraud_rows,
        },
        "evaluation": {
            "architecture": serving_fit.architecture,
            "fit_scope": "TEMPORAL_TRAINING_PARTITION",
            "evaluation_scope": "OUT_OF_TIME_HOLDOUT",
            "visualization_path": (
                "evaluation/selected_candidate_score_distribution.png"
            ),
            "source_candidate_model_path": (
                f"candidates/{serving_fit.architecture}/model.pkl"
            ),
            "source_candidate_metrics_path": (
                f"candidates/{serving_fit.architecture}/metrics.json"
            ),
        },
        "artifacts": [
            {
                **artifact_descriptor(path, role),
                "relative_path": path.relative_to(bundle_dir).as_posix(),
            }
            for path, role in artifacts
        ],
    }
    write_manifest(manifest_path, manifest)
    artifacts.append((manifest_path, "operational_manifest"))
    return bundle_dir, artifacts
