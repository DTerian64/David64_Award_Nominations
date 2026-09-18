"""Leakage-safe category target encoding for Tabular model inputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(slots=True)
class CategoryFraudRateEncoder:
    """Fit category rates on training labels and transform future rows."""

    category_rates: dict[Any, float] = field(default_factory=dict)
    global_rate: float = 0.0
    fitted: bool = False

    def fit_transform_training(
        self,
        categories: pd.Series,
        target: pd.Series,
    ) -> pd.Series:
        if not categories.index.equals(target.index):
            raise ValueError("Category and target indices differ")
        numeric_target = target.astype(int)
        self.global_rate = float(numeric_target.mean()) if len(target) else 0.0
        observed = pd.DataFrame(
            {"category": categories, "target": numeric_target},
            index=categories.index,
        ).dropna(subset=["category"])
        grouped = observed.groupby("category")["target"].agg(["sum", "count"])
        self.category_rates = {
            category: float(row["sum"] / row["count"])
            for category, row in grouped.iterrows()
        }
        self.fitted = True

        values: list[float] = []
        total_sum = int(numeric_target.sum())
        total_count = len(numeric_target)
        for index, category in categories.items():
            target_value = int(numeric_target.loc[index])
            if pd.notna(category) and category in grouped.index:
                category_sum = int(grouped.loc[category, "sum"])
                category_count = int(grouped.loc[category, "count"])
                if category_count > 1:
                    values.append(
                        float((category_sum - target_value) / (category_count - 1))
                    )
                    continue
            if total_count > 1:
                values.append(float((total_sum - target_value) / (total_count - 1)))
            else:
                values.append(0.0)
        return pd.Series(values, index=categories.index, dtype=float)

    def transform(self, categories: pd.Series) -> pd.Series:
        if not self.fitted:
            raise ValueError("CategoryFraudRateEncoder has not been fitted")
        return categories.map(self.category_rates).fillna(self.global_rate).astype(float)
