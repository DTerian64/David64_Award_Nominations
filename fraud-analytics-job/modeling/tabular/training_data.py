"""One shared, leakage-safe holdout preparation for all Tabular candidates."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from feature_builders import TabularFeatureDataset
from feature_builders.tabular.category_encoding import CategoryFraudRateEncoder

from .contracts import TabularTrainingPolicy, TemporalHoldout
from .splits import supervised_temporal_holdout


@dataclass(slots=True)
class PreparedTabularHoldout:
    holdout: TemporalHoldout
    x_train: pd.DataFrame
    x_evaluation: pd.DataFrame
    y_train: pd.Series
    y_evaluation: pd.Series
    category_encoder: CategoryFraudRateEncoder


def prepare_tabular_holdout(
    dataset: TabularFeatureDataset,
    policy: TabularTrainingPolicy,
) -> tuple[PreparedTabularHoldout | None, int, str | None]:
    """Prepare the exact rows and fitted shared transforms used by candidates."""

    eligible, holdout, reason = supervised_temporal_holdout(dataset, policy)
    if holdout is None:
        return None, len(eligible), reason
    if "CategoryId" not in dataset.frame.columns:
        raise ValueError("Tabular audit frame lacks CategoryId for fitted encoding")

    train_index = list(holdout.train_index)
    evaluation_index = list(holdout.evaluation_index)
    y_train = dataset.target.loc[train_index].astype(int)
    y_evaluation = dataset.target.loc[evaluation_index].astype(int)
    category_encoder = CategoryFraudRateEncoder()
    x_train = dataset.features.loc[train_index].copy()
    x_evaluation = dataset.features.loc[evaluation_index].copy()
    x_train["CategoryFraudRate"] = category_encoder.fit_transform_training(
        dataset.frame.loc[train_index, "CategoryId"], y_train
    )
    x_evaluation["CategoryFraudRate"] = category_encoder.transform(
        dataset.frame.loc[evaluation_index, "CategoryId"]
    )
    return (
        PreparedTabularHoldout(
            holdout=holdout,
            x_train=x_train,
            x_evaluation=x_evaluation,
            y_train=y_train,
            y_evaluation=y_evaluation,
            category_encoder=category_encoder,
        ),
        len(eligible),
        None,
    )
