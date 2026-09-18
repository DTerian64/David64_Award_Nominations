"""Non-serving Random Forest candidate over the shared Tabular-v1 matrix."""

from __future__ import annotations

from sklearn.ensemble import RandomForestClassifier

from feature_builders import TabularFeatureDataset

from .contracts import TabularCandidateResult, TabularTrainingPolicy
from .metrics import probability_metrics
from .preprocessing import RandomForestPreprocessor
from .training_data import PreparedTabularHoldout, prepare_tabular_holdout

ARCHITECTURE = "random_forest"


def new_random_forest_model(policy: TabularTrainingPolicy) -> RandomForestClassifier:
    """Construct the configured RF architecture for evaluation or serving refit."""
    return RandomForestClassifier(
        n_estimators=policy.rf_estimators,
        max_depth=policy.rf_max_depth,
        min_samples_split=policy.rf_min_samples_split,
        min_samples_leaf=policy.rf_min_samples_leaf,
        class_weight="balanced",
        random_state=policy.random_seed,
        n_jobs=-1,
    )


def train_random_forest_candidate(
    dataset: TabularFeatureDataset,
    policy: TabularTrainingPolicy | None = None,
    *,
    prepared: PreparedTabularHoldout | None = None,
) -> TabularCandidateResult:
    """Train and evaluate an in-memory candidate without publishing artifacts."""

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

    holdout = prepared.holdout
    y_train = prepared.y_train
    y_evaluation = prepared.y_evaluation

    preprocessor = RandomForestPreprocessor(
        dataset.schema.feature_columns,
        category_encoder=prepared.category_encoder,
    )
    transformed_train = preprocessor.fit_transform(prepared.x_train)
    transformed_evaluation = preprocessor.transform(prepared.x_evaluation)
    model = new_random_forest_model(policy)
    model.fit(transformed_train, y_train)
    probabilities = model.predict_proba(transformed_evaluation)[:, 1]
    metrics = {
        **probability_metrics(y_evaluation, probabilities),
        "training_rows": len(holdout.train_index),
        "evaluation_rows": len(holdout.evaluation_index),
        "training_fraud_rows": int(y_train.sum()),
        "evaluation_fraud_rows": int(y_evaluation.sum()),
        "tree_node_count": int(sum(tree.tree_.node_count for tree in model.estimators_)),
        "feature_count": len(dataset.schema.feature_columns),
        "random_seed": policy.random_seed,
        "category_encoder_global_rate": prepared.category_encoder.global_rate,
        "category_encoder_category_count": len(
            prepared.category_encoder.category_rates
        ),
    }
    return TabularCandidateResult(
        architecture=ARCHITECTURE,
        status="COMPLETED",
        feature_schema_id=dataset.schema.schema_id,
        source_snapshot_id=dataset.source_snapshot_id,
        policy=policy,
        holdout=holdout,
        model=model,
        preprocessor=preprocessor,
        metrics=metrics,
        evaluation_probabilities=tuple(float(value) for value in probabilities),
        evaluation_targets=tuple(int(value) for value in y_evaluation),
    )
