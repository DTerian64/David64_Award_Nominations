"""Deterministic canonical-dataset identity and snapshot construction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from .contracts import (
    Actor,
    DatasetSnapshot,
    EventParticipant,
    IntegrityEvent,
    OutcomeLabel,
    Relationship,
    as_utc,
)

CANONICAL_SCHEMA_VERSION = "integrity-data-v1"


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return as_utc(value).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_safe(item) for item in value)
    return value


def _sorted_records(records: Iterable[Any], *keys: str) -> list[dict[str, Any]]:
    payload = [_json_safe(asdict(record)) for record in records]
    return sorted(payload, key=lambda row: tuple(str(row[key]) for key in keys))


def compute_records_sha256(
    *,
    actors: Iterable[Actor],
    events: Iterable[IntegrityEvent],
    participants: Iterable[EventParticipant],
    relationships: Iterable[Relationship],
    labels: Iterable[OutcomeLabel],
) -> str:
    """Hash canonical records independent of database or input row ordering."""

    payload = {
        "actors": _sorted_records(actors, "source_system", "actor_id"),
        "events": _sorted_records(events, "source_system", "event_id"),
        "participants": _sorted_records(
            participants,
            "source_system",
            "event_id",
            "source_role",
            "actor_id",
        ),
        "relationships": _sorted_records(
            relationships,
            "source_system",
            "relationship_id",
        ),
        "labels": _sorted_records(labels, "source_system", "event_id"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_snapshot(
    *,
    tenant_id: int,
    source_system: str,
    adapter_name: str,
    adapter_version: str,
    as_of_exclusive: datetime,
    window_start_inclusive: datetime | None,
    capabilities: frozenset[str],
    is_synthetic_tenant: bool,
    actors: tuple[Actor, ...],
    events: tuple[IntegrityEvent, ...],
    participants: tuple[EventParticipant, ...],
    relationships: tuple[Relationship, ...],
    labels: tuple[OutcomeLabel, ...],
) -> DatasetSnapshot:
    records_sha256 = compute_records_sha256(
        actors=actors,
        events=events,
        participants=participants,
        relationships=relationships,
        labels=labels,
    )
    as_of = as_utc(as_of_exclusive)
    counts = {
        "actors": len(actors),
        "events": len(events),
        "participants": len(participants),
        "relationships": len(relationships),
        "labels": len(labels),
    }
    snapshot_id = (
        f"{source_system.lower()}-t{tenant_id}-"
        f"{as_of:%Y%m%dT%H%M%SZ}-{records_sha256[:12]}"
    )
    return DatasetSnapshot(
        snapshot_id=snapshot_id,
        tenant_id=tenant_id,
        source_system=source_system,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        canonical_schema_version=CANONICAL_SCHEMA_VERSION,
        as_of_exclusive=as_of,
        window_start_inclusive=(
            as_utc(window_start_inclusive)
            if window_start_inclusive is not None
            else None
        ),
        capabilities=capabilities,
        record_counts=counts,
        records_sha256=records_sha256,
        is_synthetic_tenant=is_synthetic_tenant,
    )
