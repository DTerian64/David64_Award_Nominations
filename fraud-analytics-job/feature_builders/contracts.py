"""Contracts between canonical source data and model-family trainers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import pandas as pd

from integrity_data import IntegrityDataset


@dataclass(frozen=True, slots=True)
class TabularFeatureSchema:
    """Identity and requirements of one ordered tabular feature contract."""

    name: str
    version: str
    source_system: str
    required_capabilities: frozenset[str]
    feature_columns: tuple[str, ...]
    target_column: str = "IsFraud"

    @property
    def schema_id(self) -> str:
        return f"{self.name}:{self.version}"


@dataclass(slots=True)
class TabularFeatureDataset:
    """One immutable-in-identity, mutable-in-memory pandas feature dataset.

    ``frame`` retains event identity and audit columns. ``features`` contains
    only the ordered model inputs declared by ``schema``. Builders never train
    a model or select a serving candidate.
    """

    schema: TabularFeatureSchema
    source_snapshot_id: str
    frame: pd.DataFrame
    features: pd.DataFrame
    target: pd.Series
    fitted_state: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        expected = list(self.schema.feature_columns)
        if list(self.features.columns) != expected:
            raise ValueError(
                f"Feature columns do not match {self.schema.schema_id}: "
                f"expected {expected}, received {list(self.features.columns)}"
            )
        if len(self.frame) != len(self.features) or len(self.frame) != len(self.target):
            raise ValueError("Feature frame, matrix, and target lengths differ")
        if not self.frame.index.equals(self.features.index):
            raise ValueError("Feature matrix index does not match its audit frame")
        if not self.frame.index.equals(self.target.index):
            raise ValueError("Target index does not match its audit frame")


class FeatureBuilder(Protocol):
    """Source-neutral interface implemented by every feature builder."""

    schema: TabularFeatureSchema

    def build(self, dataset: IntegrityDataset, **kwargs: Any) -> TabularFeatureDataset:
        """Build features from a validated canonical snapshot without training."""
        ...
