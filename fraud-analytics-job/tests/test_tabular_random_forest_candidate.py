"""T3 database-free Random Forest candidate tests."""

from __future__ import annotations

import inspect
import pickle
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feature_builders import TabularFeatureDataset
from feature_builders.tabular import AWARD_NOMINATION_TABULAR_V1_SCHEMA
from feature_builders.tabular.category_encoding import CategoryFraudRateEncoder
from modeling.tabular import TabularTrainingPolicy, train_random_forest_candidate
from modeling.tabular import random_forest as random_forest_module


def _policy() -> TabularTrainingPolicy:
    return TabularTrainingPolicy(
        evaluation_fraction=0.20,
        minimum_training_samples=60,
        minimum_evaluation_samples=20,
        minimum_class_samples_per_split=2,
        rf_estimators=12,
        rf_max_depth=6,
        rf_min_samples_split=2,
        rf_min_samples_leaf=1,
    )


def _feature_dataset(count: int = 120) -> TabularFeatureDataset:
    schema = AWARD_NOMINATION_TABULAR_V1_SCHEMA
    positions = np.arange(count)
    target = pd.Series((positions % 5 == 0).astype(int), dtype="Int64")
    day = positions % 7
    month = positions % 12
    features = pd.DataFrame(
        {
            "Amount": 100 + positions * 3 + target.astype(int) * 800,
            "DayOfWeekSin": np.sin(2 * np.pi * day / 7),
            "DayOfWeekCos": np.cos(2 * np.pi * day / 7),
            "MonthSin": np.sin(2 * np.pi * month / 12),
            "MonthCos": np.cos(2 * np.pi * month / 12),
            "IsWeekend": np.isin(day, [5, 6]).astype(int),
            "NominatorTotalNominations": 1 + positions % 13,
            "NominatorAvgAmount": 150 + positions % 17,
            "NominatorStdAmount": 5 + positions % 7,
            "NominatorUniqueBeneficiaries": 1 + positions % 9,
            "BeneficiaryTotalReceived": 1 + positions % 11,
            "BeneficiaryAvgAmountReceived": 130 + positions % 19,
            "HasReciprocalNomination": (positions % 10 == 0).astype(int),
            "PairNominationCount": 1 + positions % 4,
            "AmountZScore": (positions - positions.mean()) / positions.std(),
            "IsHighAmount": (positions > count * 0.9).astype(int),
            "NominatorConcentrationRatio": (1 + positions % 13)
            / (2 + positions % 9),
            "CategoryFraudRate": (positions % 5 == 0).astype(float) * 0.2,
            "DescriptionCosineSim": (positions % 10) / 10,
            "DescriptionEmbDistance": 1 - (positions % 10) / 10,
            "TransactionalPhraseScore": (positions % 3) / 6,
        },
        columns=list(schema.feature_columns),
    )
    features.loc[3, "NominatorStdAmount"] = np.nan
    start = datetime(2026, 1, 1)
    frame = pd.DataFrame(
        {
            "NominationId": positions + 1000,
            "NominationDate": [start + timedelta(days=int(i)) for i in positions],
            "LabelSource": ["synthetic_ground_truth"] * count,
            "CategoryId": 1 + positions % 4,
            "IsFraud": target,
        }
    )
    result = TabularFeatureDataset(
        schema=schema,
        source_snapshot_id="award_nomination-t5-fixture",
        frame=frame,
        features=features,
        target=target,
    )
    result.validate()
    return result


def test_random_forest_candidate_uses_out_of_time_holdout_and_shared_schema():
    result = train_random_forest_candidate(_feature_dataset(), _policy())

    assert result.completed
    assert result.architecture == "random_forest"
    assert result.feature_schema_id == "award-nomination-tabular:tabular-v1"
    assert result.holdout is not None
    assert max(result.holdout.train_nomination_ids) < min(
        result.holdout.evaluation_nomination_ids
    )
    assert result.holdout.train_end <= result.holdout.evaluation_start
    assert result.metrics["feature_count"] == 21
    assert result.metrics["selection_metric"] == "holdout_pr_auc"
    assert 0 <= result.metrics["holdout_pr_auc"] <= 1
    assert 0 <= result.metrics["holdout_brier_score"] <= 1
    assert len(result.evaluation_probabilities) == result.metrics["evaluation_rows"]


def test_candidate_and_preprocessor_are_deterministic_and_pickle_safe():
    dataset = _feature_dataset()
    first = train_random_forest_candidate(dataset, _policy())
    second = train_random_forest_candidate(dataset, _policy())

    assert first.evaluation_probabilities == second.evaluation_probabilities
    restored = pickle.loads(pickle.dumps(first.preprocessor))
    evaluation = dataset.features.loc[list(first.holdout.evaluation_index)]
    np.testing.assert_allclose(
        restored.transform(evaluation),
        first.preprocessor.transform(evaluation),
    )
    assert restored.category_encoder.category_rates == (
        first.preprocessor.category_encoder.category_rates
    )


def test_category_rates_are_leave_one_out_for_training_and_train_only_for_holdout():
    categories = pd.Series(["A", "A", "B", "B"])
    target = pd.Series([1, 0, 1, 1])
    encoder = CategoryFraudRateEncoder()

    training = encoder.fit_transform_training(categories, target)
    evaluation = encoder.transform(pd.Series(["A", "B", "UNKNOWN", None]))

    assert training.tolist() == [0.0, 1.0, 1.0, 1.0]
    assert evaluation.tolist() == [0.5, 1.0, 0.75, 0.75]


def test_candidate_skips_when_temporal_label_volume_is_insufficient():
    result = train_random_forest_candidate(_feature_dataset(40), _policy())

    assert not result.completed
    assert result.status == "SKIPPED"
    assert result.reason_code == "INSUFFICIENT_TEMPORAL_LABELS"
    assert result.model is None
    assert result.preprocessor is None


def test_preprocessor_rejects_feature_reordering():
    dataset = _feature_dataset()
    result = train_random_forest_candidate(dataset, _policy())
    reordered = dataset.features.loc[:, list(reversed(dataset.features.columns))]

    with pytest.raises(ValueError, match="feature order changed"):
        result.preprocessor.transform(reordered)


def test_candidate_module_has_no_database_or_publication_dependency():
    source = inspect.getsource(random_forest_module)

    assert "dbo." not in source
    assert "get_db_connection" not in source
    assert "upload" not in source.lower()
    assert "activate" not in source.lower()
