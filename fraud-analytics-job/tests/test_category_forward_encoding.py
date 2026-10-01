import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score
from systems.award_nominations.features.tabular.category_encoding import (
    CategoryFraudRateEncoder,
)
from systems.award_nominations.modeling.tabular.metrics import (
    holdout_permutation_importance,
)


def test_balanced_categories_no_longer_encode_the_rows_own_class():
    # Each chronological block has the same 2% category rate.
    categories = pd.Series([f"C{j % 5}" for block in range(5) for j in range(1000)])
    target = pd.Series([int(j % 250 < 5) for block in range(5) for j in range(1000)])
    times = pd.Series(pd.date_range("2025-01-01", periods=len(target), freq="h", tz="UTC"))
    encoder = CategoryFraudRateEncoder()
    encoded = encoder.fit_transform_training(categories, target, occurred_at=times)
    assert np.allclose(encoded, .02)
    model = RandomForestClassifier(n_estimators=8, random_state=0).fit(encoded.to_numpy().reshape(-1, 1), target)
    evaluation_categories = pd.Series([f"C{j % 5}" for j in range(1000)])
    evaluation_target = pd.Series([int(j % 250 < 5) for j in range(1000)])
    evaluation = encoder.transform(evaluation_categories).to_numpy().reshape(-1, 1)
    assert average_precision_score(evaluation_target, model.predict_proba(evaluation)[:, 1]) == .02
    importance = holdout_permutation_importance(model, evaluation, evaluation_target, ("CategoryFraudRate",), repeats=3, random_seed=0)
    assert importance["features"][0]["pr_auc_decrease_mean"] == 0


def test_changing_current_or_future_labels_cannot_change_earlier_fold_inputs():
    categories = pd.Series(["A"] * 100)
    times = pd.Series(pd.date_range("2026-01-01", periods=100, freq="h", tz="UTC"))
    target = pd.Series([i % 2 for i in range(100)])
    original = CategoryFraudRateEncoder().fit_transform_training(categories, target, occurred_at=times)
    changed = target.copy()
    changed.iloc[40:] = 1 - changed.iloc[40:]
    updated = CategoryFraudRateEncoder().fit_transform_training(categories, changed, occurred_at=times)
    assert np.allclose(original.iloc[:60], updated.iloc[:60])


def test_label_availability_and_equal_timestamps_are_respected():
    categories = pd.Series(["A"] * 6)
    times = pd.Series(pd.to_datetime(["2026-01-01"] * 2 + ["2026-01-02"] * 2 + ["2026-01-03"] * 2, utc=True))
    known = times + pd.Timedelta(days=5)
    encoder = CategoryFraudRateEncoder()
    encoded = encoder.fit_transform_training(categories, pd.Series([0, 1] * 3), occurred_at=times, known_at=known, fit_cutoff="2026-01-04")
    assert encoded.tolist() == [.02] * 6
    assert encoder.diagnostics["fitted_known_label_count"] == 0
    assert encoder.transform(pd.Series(["A", "NEW"])).tolist() == [.02, .02]
