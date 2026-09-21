"""Non-serving Tabular MLP candidate over the shared Tabular-v1 matrix."""

from __future__ import annotations

from copy import deepcopy

import numpy as np
from sklearn.neural_network import MLPClassifier
from sklearn.utils.class_weight import compute_sample_weight

from feature_builders import TabularFeatureDataset

from .contracts import TabularCandidateResult, TabularTrainingPolicy
from .metrics import holdout_permutation_importance, probability_metrics
from .preprocessing import MlpPreprocessor
from .training_data import PreparedTabularHoldout, prepare_tabular_holdout

ARCHITECTURE = "tabular_mlp"


def new_tabular_mlp_model(
    policy: TabularTrainingPolicy, sample_count: int
) -> MLPClassifier:
    """Construct the configured Tabular MLP for evaluation or serving refit."""
    return MLPClassifier(
        hidden_layer_sizes=policy.mlp_hidden_layers,
        activation="relu",
        solver="adam",
        alpha=policy.mlp_alpha,
        batch_size=min(policy.mlp_batch_size, sample_count),
        learning_rate_init=policy.mlp_learning_rate,
        max_iter=policy.mlp_max_iterations,
        early_stopping=True,
        validation_fraction=policy.mlp_validation_fraction,
        n_iter_no_change=policy.mlp_patience,
        random_state=policy.random_seed,
    )


def train_tabular_mlp_candidate(
    dataset: TabularFeatureDataset,
    policy: TabularTrainingPolicy | None = None,
    *,
    prepared: PreparedTabularHoldout | None = None,
) -> TabularCandidateResult:
    """Train and evaluate an in-memory MLP without publishing artifacts."""

    policy = policy or TabularTrainingPolicy()
    eligible_count = len(dataset.frame)
    reason = None
    if prepared is None:
        prepared, eligible_count, reason = prepare_tabular_holdout(dataset, policy)
    if prepared is None:
        return TabularCandidateResult(
            architecture=ARCHITECTURE,
            status="SKIPPED",
            feature_schema_id=dataset.schema.schema_id,
            source_snapshot_id=dataset.source_snapshot_id,
            policy=policy,
            reason_code="INSUFFICIENT_TEMPORAL_LABELS",
            reason_detail=reason,
            metrics={"supervised_rows": eligible_count},
        )

    preprocessor = MlpPreprocessor(
        dataset.schema.feature_columns,
        category_encoder=deepcopy(prepared.category_encoder),
    )
    transformed_train = preprocessor.fit_transform(prepared.x_train)
    transformed_evaluation = preprocessor.transform(prepared.x_evaluation)
    sample_weights = compute_sample_weight(
        class_weight="balanced",
        y=prepared.y_train.to_numpy(dtype=int),
    )
    model = new_tabular_mlp_model(policy, len(prepared.y_train))
    model.fit(
        transformed_train,
        prepared.y_train.to_numpy(dtype=int),
        sample_weight=sample_weights,
    )
    probabilities = model.predict_proba(transformed_evaluation)[:, 1]
    permutation = holdout_permutation_importance(
        model,
        transformed_evaluation,
        prepared.y_evaluation,
        preprocessor.feature_columns,
        repeats=policy.permutation_importance_repeats,
        random_seed=policy.random_seed,
    )
    parameter_count = int(
        sum(weights.size for weights in model.coefs_)
        + sum(bias.size for bias in model.intercepts_)
    )
    metrics = {
        **probability_metrics(prepared.y_evaluation, probabilities),
        "training_rows": len(prepared.holdout.train_index),
        "evaluation_rows": len(prepared.holdout.evaluation_index),
        "training_fraud_rows": int(prepared.y_train.sum()),
        "evaluation_fraud_rows": int(prepared.y_evaluation.sum()),
        "parameter_count": parameter_count,
        "feature_count": len(dataset.schema.feature_columns),
        "random_seed": policy.random_seed,
        "iterations_run": int(model.n_iter_),
        "final_training_loss": float(model.loss_),
        "category_encoder_global_rate": prepared.category_encoder.global_rate,
        "category_encoder_category_count": len(
            prepared.category_encoder.category_rates
        ),
        "permutation_importance": permutation,
    }
    if not np.isfinite(model.loss_):
        raise ValueError("Tabular MLP emitted a non-finite training loss")
    return TabularCandidateResult(
        architecture=ARCHITECTURE,
        status="COMPLETED",
        feature_schema_id=dataset.schema.schema_id,
        source_snapshot_id=dataset.source_snapshot_id,
        policy=policy,
        holdout=prepared.holdout,
        model=model,
        preprocessor=preprocessor,
        metrics=metrics,
        evaluation_probabilities=tuple(float(value) for value in probabilities),
        evaluation_targets=tuple(int(value) for value in prepared.y_evaluation),
    )
