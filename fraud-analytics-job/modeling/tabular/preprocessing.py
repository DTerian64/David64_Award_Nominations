"""Persistable architecture-specific preprocessing for Tabular candidates."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

from feature_builders.tabular.category_encoding import CategoryFraudRateEncoder


def _numeric_matrix(frame: pd.DataFrame) -> np.ndarray:
    numeric = frame.apply(pd.to_numeric, errors="raise")
    return numeric.to_numpy(dtype=float, na_value=np.nan)


@dataclass(slots=True)
class RandomForestPreprocessor:
    """Current RF-compatible zero imputation followed by fitted scaling."""

    feature_columns: tuple[str, ...]
    imputer: SimpleImputer = field(
        default_factory=lambda: SimpleImputer(strategy="constant", fill_value=0.0)
    )
    scaler: StandardScaler = field(default_factory=StandardScaler)
    category_encoder: CategoryFraudRateEncoder | None = None
    fitted: bool = False

    def _ordered(self, frame: pd.DataFrame) -> pd.DataFrame:
        received = tuple(frame.columns)
        if received != self.feature_columns:
            raise ValueError(
                "Tabular feature order changed: "
                f"expected {self.feature_columns}, received {received}"
            )
        return frame.loc[:, list(self.feature_columns)]

    def fit(self, frame: pd.DataFrame) -> "RandomForestPreprocessor":
        matrix = _numeric_matrix(self._ordered(frame))
        imputed = self.imputer.fit_transform(matrix)
        self.scaler.fit(imputed)
        self.fitted = True
        return self

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        if not self.fitted:
            raise ValueError("RandomForestPreprocessor has not been fitted")
        matrix = _numeric_matrix(self._ordered(frame))
        return self.scaler.transform(self.imputer.transform(matrix))

    def fit_transform(self, frame: pd.DataFrame) -> np.ndarray:
        return self.fit(frame).transform(frame)


@dataclass(slots=True)
class MlpPreprocessor(RandomForestPreprocessor):
    """MLP preprocessing kept distinct so its contract can evolve independently."""
