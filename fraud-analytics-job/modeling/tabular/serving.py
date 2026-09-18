"""Full matured-label refit for the selected Tabular architecture."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sklearn.utils.class_weight import compute_sample_weight

from feature_builders import TabularFeatureDataset
from feature_builders.tabular.category_encoding import CategoryFraudRateEncoder

from .contracts import TabularTrainingPolicy
from .preprocessing import MlpPreprocessor, RandomForestPreprocessor
from .random_forest import new_random_forest_model
from .tabular_mlp import new_tabular_mlp_model


@dataclass(slots=True)
class TabularServingFit:
    architecture: str
    model: Any
    preprocessor: Any
    training_rows: int
    training_fraud_rows: int


def fit_selected_for_serving(
    dataset: TabularFeatureDataset,
    architecture: str,
    policy: TabularTrainingPolicy,
) -> TabularServingFit:
    """Refit the winner over every eligible supervised row."""

    index = dataset.target[dataset.target.notna()].index
    if len(index) == 0:
        raise ValueError("No supervised Tabular rows are available for serving refit")
    target = dataset.target.loc[index].astype(int)
    if target.nunique() != 2:
        raise ValueError("Serving refit requires both outcome classes")

    encoder = CategoryFraudRateEncoder()
    features = dataset.features.loc[index].copy()
    features["CategoryFraudRate"] = encoder.fit_transform_training(
        dataset.frame.loc[index, "CategoryId"], target
    )

    if architecture == "random_forest":
        preprocessor = RandomForestPreprocessor(
            dataset.schema.feature_columns,
            category_encoder=deepcopy(encoder),
        )
        model = new_random_forest_model(policy)
        transformed = preprocessor.fit_transform(features)
        model.fit(transformed, target)
    elif architecture == "tabular_mlp":
        preprocessor = MlpPreprocessor(
            dataset.schema.feature_columns,
            category_encoder=deepcopy(encoder),
        )
        model = new_tabular_mlp_model(policy, len(target))
        transformed = preprocessor.fit_transform(features)
        model.fit(
            transformed,
            target,
            sample_weight=compute_sample_weight(
                class_weight="balanced", y=target.to_numpy(dtype=int)
            ),
        )
    else:
        raise ValueError(f"Unsupported Tabular serving architecture: {architecture}")

    return TabularServingFit(
        architecture=architecture,
        model=model,
        preprocessor=preprocessor,
        training_rows=len(target),
        training_fraud_rows=int(target.sum()),
    )
