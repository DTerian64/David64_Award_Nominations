"""T4 Tabular MLP evaluation and candidate-selection tests."""

from __future__ import annotations

import inspect
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modeling.tabular import (
    TabularSelectionPolicy,
    TabularTrainingPolicy,
    evaluate_tabular_candidates,
    train_tabular_mlp_candidate,
)
from modeling.tabular import tabular_mlp as mlp_module
from tests.test_tabular_random_forest_candidate import _feature_dataset


def _t4_policy() -> TabularTrainingPolicy:
    return TabularTrainingPolicy(
        evaluation_fraction=0.20,
        minimum_training_samples=60,
        minimum_evaluation_samples=20,
        minimum_class_samples_per_split=2,
        permutation_importance_repeats=3,
        rf_estimators=12,
        rf_max_depth=6,
        rf_min_samples_split=2,
        rf_min_samples_leaf=1,
        mlp_hidden_layers=(16, 8),
        mlp_batch_size=32,
        mlp_max_iterations=80,
        mlp_patience=8,
    )


def test_mlp_candidate_is_deterministic_and_persistable():
    dataset = _feature_dataset()
    first = train_tabular_mlp_candidate(dataset, _t4_policy())
    second = train_tabular_mlp_candidate(dataset, _t4_policy())

    assert first.completed
    assert first.architecture == "tabular_mlp"
    assert first.evaluation_probabilities == second.evaluation_probabilities
    assert first.metrics["parameter_count"] > 0
    assert first.metrics["iterations_run"] <= _t4_policy().mlp_max_iterations
    restored_model = pickle.loads(pickle.dumps(first.model))
    restored_preprocessor = pickle.loads(pickle.dumps(first.preprocessor))
    evaluation = dataset.features.loc[list(first.holdout.evaluation_index)].copy()
    evaluation["CategoryFraudRate"] = restored_preprocessor.category_encoder.transform(
        dataset.frame.loc[list(first.holdout.evaluation_index), "CategoryId"]
    )
    probabilities = restored_model.predict_proba(
        restored_preprocessor.transform(evaluation)
    )[:, 1]
    np.testing.assert_allclose(probabilities, first.evaluation_probabilities)


def test_candidates_share_holdout_and_selection_matches_policy():
    evaluation = evaluate_tabular_candidates(_feature_dataset(), _t4_policy())
    rf = evaluation.candidates["random_forest"]
    mlp = evaluation.candidates["tabular_mlp"]

    assert rf.completed and mlp.completed
    assert rf.holdout == mlp.holdout
    assert rf.evaluation_targets == mlp.evaluation_targets
    assert evaluation.selection.status == "SELECTED"
    assert not evaluation.selection.serving_state_changed

    expected_features = set(_feature_dataset().schema.feature_columns)
    for candidate in (rf, mlp):
        importance = candidate.metrics["permutation_importance"]
        assert importance["method"] == "PERMUTATION_IMPORTANCE"
        assert importance["scoring"] == "average_precision"
        assert importance["score_semantics"] == "decrease_in_holdout_pr_auc"
        assert importance["holdout_row_count"] == len(
            candidate.evaluation_targets
        )
        assert importance["repeats"] == _t4_policy().permutation_importance_repeats
        assert importance["random_seed"] == _t4_policy().random_seed
        assert {row["name"] for row in importance["features"]} == expected_features
        assert all(
            np.isfinite(row["pr_auc_decrease_mean"])
            and np.isfinite(row["pr_auc_decrease_std"])
            for row in importance["features"]
        )

    rf_score = rf.metrics["holdout_pr_auc"]
    mlp_score = mlp.metrics["holdout_pr_auc"]
    if abs(rf_score - mlp_score) <= 0.005:
        assert evaluation.selection.selected_architecture == "random_forest"
        assert "TIE" in evaluation.selection.selection_reason
    else:
        expected = "random_forest" if rf_score > mlp_score else "tabular_mlp"
        assert evaluation.selection.selected_architecture == expected
        assert evaluation.selection.selection_reason == "HIGHEST_ELIGIBLE_PR_AUC"


def test_selection_can_reject_both_candidates_without_serving_fallback():
    evaluation = evaluate_tabular_candidates(
        _feature_dataset(),
        _t4_policy(),
        TabularSelectionPolicy(minimum_pr_auc_lift=1000),
    )

    assert evaluation.selection.status == "NO_ELIGIBLE_CANDIDATE"
    assert evaluation.selection.selected_architecture is None
    assert evaluation.selection.selection_reason == "NO_CANDIDATE_PASSED_GUARDRAILS"
    assert not evaluation.selection.serving_state_changed


def test_mlp_module_has_no_database_or_publication_dependency():
    source = inspect.getsource(mlp_module)

    assert "dbo." not in source
    assert "get_db_connection" not in source
    assert "upload" not in source.lower()
    assert "activate" not in source.lower()
