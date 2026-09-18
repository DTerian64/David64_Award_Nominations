"""Canonical, source-neutral integrity data contracts.

These records intentionally contain no pandas, SQL, PyTorch, or model-specific
types. Source adapters create them; feature builders consume them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping


def as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    SQL Server ``DATETIME2`` values arrive without a timezone. Application
    timestamps are UTC, so naive values are interpreted as UTC rather than as
    the host machine's local timezone.
    """

    if not isinstance(value, datetime):
        raise TypeError(f"Expected datetime, received {type(value).__name__}")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Actor:
    tenant_id: int
    source_system: str
    actor_id: str
    actor_type: str = "PERSON"
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntegrityEvent:
    tenant_id: int
    source_system: str
    event_id: str
    event_type: str
    occurred_at: datetime
    known_at: datetime
    amount: Decimal | None = None
    currency: str | None = None
    category: str | None = None
    text: str | None = None
    status: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EventParticipant:
    tenant_id: int
    source_system: str
    event_id: str
    actor_id: str
    source_role: str
    normalized_role: str


@dataclass(frozen=True, slots=True)
class Relationship:
    tenant_id: int
    source_system: str
    relationship_id: str
    source_actor_id: str
    target_actor_id: str
    relationship_type: str
    known_at: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutcomeLabel:
    tenant_id: int
    source_system: str
    event_id: str
    disposition: str
    provenance: str
    known_at: datetime
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    behavior_labels: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DatasetSnapshot:
    snapshot_id: str
    tenant_id: int
    source_system: str
    adapter_name: str
    adapter_version: str
    canonical_schema_version: str
    as_of_exclusive: datetime
    window_start_inclusive: datetime | None
    capabilities: frozenset[str]
    record_counts: Mapping[str, int]
    records_sha256: str
    is_synthetic_tenant: bool = False


@dataclass(frozen=True, slots=True)
class IntegrityDataset:
    snapshot: DatasetSnapshot
    actors: tuple[Actor, ...]
    events: tuple[IntegrityEvent, ...]
    participants: tuple[EventParticipant, ...]
    relationships: tuple[Relationship, ...]
    labels: tuple[OutcomeLabel, ...]
