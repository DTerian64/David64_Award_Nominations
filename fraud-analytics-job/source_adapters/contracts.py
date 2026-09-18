"""Contracts implemented by every source-system adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from integrity_data import IntegrityDataset, as_utc


class SourceCapability(StrEnum):
    DIRECTED_ACTOR_PAIR = "HAS_DIRECTED_ACTOR_PAIR"
    AMOUNT = "HAS_AMOUNT"
    CURRENCY = "HAS_CURRENCY"
    CATEGORY = "HAS_CATEGORY"
    TEXT = "HAS_TEXT"
    EVENT_STATUS = "HAS_EVENT_STATUS"
    HIERARCHY_RELATIONSHIPS = "HAS_HIERARCHY_RELATIONSHIPS"
    REVIEWED_OUTCOMES = "HAS_REVIEWED_OUTCOMES"
    BEHAVIOR_LABELS = "HAS_BEHAVIOR_LABELS"


@dataclass(frozen=True, slots=True)
class SourceReadRequest:
    tenant_id: int
    as_of_exclusive: datetime
    window_days: int | None = None

    def __post_init__(self) -> None:
        if self.tenant_id <= 0:
            raise ValueError("tenant_id must be positive")
        if self.window_days is not None and self.window_days <= 0:
            raise ValueError("window_days must be positive when provided")
        object.__setattr__(self, "as_of_exclusive", as_utc(self.as_of_exclusive))

    @property
    def window_start_inclusive(self) -> datetime | None:
        if self.window_days is None:
            return None
        return self.as_of_exclusive - timedelta(days=self.window_days)


class SourceAdapter(Protocol):
    source_system: str
    adapter_name: str
    adapter_version: str
    capabilities: frozenset[str]

    def load(self, connection: Any, request: SourceReadRequest) -> IntegrityDataset:
        """Extract, map, validate, and return one canonical tenant snapshot."""
        ...
