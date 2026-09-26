"""Forward-only, out-of-fold category target encoding."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import numpy as np
import pandas as pd

ENCODING_CONTRACT = "category-fraud-rate-forward-oof-v1"


@dataclass(slots=True)
class CategoryFraudRateEncoder:
    """Encode a temporal fold using only labels known before that fold."""

    category_rates: dict[Any, float] = field(default_factory=dict)
    global_rate: float = 0.02
    fitted: bool = False
    smoothing: float = 20.0
    folds: int = 5
    cold_start_rate: float = 0.02
    diagnostics: dict = field(default_factory=dict)

    def _rates(self, categories: pd.Series, target: pd.Series) -> tuple[dict, float]:
        prior = float(target.mean()) if len(target) else self.cold_start_rate
        grouped = pd.DataFrame({"category": categories, "target": target}).dropna(subset=["category"]).groupby("category")["target"].agg(["sum", "count"])
        return {category: float((row["sum"] + self.smoothing * prior) / (row["count"] + self.smoothing))
                for category, row in grouped.iterrows()}, prior

    def fit_transform_training(
        self,
        categories: pd.Series,
        target: pd.Series,
        *, occurred_at: pd.Series | None = None,
        known_at: pd.Series | None = None, fit_cutoff: Any = None,
    ) -> pd.Series:
        if not categories.index.equals(target.index) or not categories.index.is_unique:
            raise ValueError("Category and target indices must be equal and unique")
        if self.folds < 2 or self.smoothing < 0 or not 0 <= self.cold_start_rate <= 1:
            raise ValueError("Invalid category encoding parameters")
        numeric_target = target.astype(int)
        if not numeric_target.isin([0, 1]).all():
            raise ValueError("Category encoding requires binary outcomes")
        # Isolated callers may use ordinal ordering. Production passes actual
        # event and label-availability timestamps from the source adapter.
        if occurred_at is None:
            occurred_at = pd.Series(pd.date_range("2000-01-01", periods=len(target), freq="s", tz="UTC"), index=target.index)
        if not occurred_at.index.equals(target.index) or (known_at is not None and not known_at.index.equals(target.index)):
            raise ValueError("Category encoding timestamp indices differ")
        times = pd.to_datetime(occurred_at, utc=True)
        availability = pd.to_datetime(known_at if known_at is not None else occurred_at, utc=True)
        if times.isna().any() or availability.isna().any():
            raise ValueError("Category encoding timestamps must be known")
        encoded = pd.Series(self.cold_start_rate, index=target.index, dtype=float)
        evidence = []
        # Never split simultaneous events between folds.
        for number, block in enumerate(np.array_split(np.sort(times.unique()), min(self.folds, max(times.nunique(), 1))), 1):
            if not len(block):
                continue
            start = pd.Timestamp(block[0])
            history = (times < start) & (availability < start)
            rates, prior = self._rates(categories.loc[history], numeric_target.loc[history])
            current = times.isin(block)
            encoded.loc[current] = categories.loc[current].map(rates).fillna(prior)
            evidence.append({"fold": number, "start": start.isoformat(), "encoded_rows": int(current.sum()), "known_history_rows": int(history.sum())})
        fit_history = pd.Series(True, index=target.index)
        if fit_cutoff is not None:
            cutoff = pd.to_datetime(fit_cutoff, utc=True)
            fit_history = (times < cutoff) & (availability < cutoff)
        self.category_rates, self.global_rate = self._rates(categories.loc[fit_history], numeric_target.loc[fit_history])
        self.fitted = True
        self.diagnostics = {"contract": ENCODING_CONTRACT, "smoothing": self.smoothing, "cold_start_rate": self.cold_start_rate,
                            "folds": evidence, "fitted_known_label_count": int(fit_history.sum())}
        return encoded

    def transform(self, categories: pd.Series) -> pd.Series:
        if not self.fitted:
            raise ValueError("CategoryFraudRateEncoder has not been fitted")
        return categories.map(self.category_rates).fillna(self.global_rate).astype(float)
