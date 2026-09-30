"""Assemble canonical datasets from source adapters and Sentinel enrichment."""

from __future__ import annotations

from typing import Any

from integrity_data import IntegrityDataset, build_snapshot, validate_dataset
from source_adapters.award_nominations import AwardNominationAdapter
from source_adapters.award_nominations.capabilities import (
    AWARD_NOMINATION_CAPABILITIES,
)
from source_adapters.award_nominations.connection import connect as connect_award
from source_adapters.contracts import SourceReadRequest

from .db import connect as connect_sentinel
from .outcomes import load_outcome_labels


def load_award_nomination_dataset(
    request: SourceReadRequest,
    *,
    source_connection: Any | None = None,
    sentinel_connection: Any | None = None,
) -> IntegrityDataset:
    """Load one source snapshot and enrich it with Sentinel-owned outcomes."""

    owns_source = source_connection is None
    owns_sentinel = sentinel_connection is None
    source_connection = source_connection or connect_award()
    sentinel_connection = sentinel_connection or connect_sentinel()
    try:
        source_dataset = AwardNominationAdapter().load(source_connection, request)
        labels = load_outcome_labels(
            sentinel_connection,
            tenant_id=request.tenant_id,
            source_system=source_dataset.snapshot.source_system,
            event_ids=(event.event_id for event in source_dataset.events),
            as_of_exclusive=request.as_of_exclusive,
            is_synthetic_tenant=source_dataset.snapshot.is_synthetic_tenant,
        )
        snapshot = build_snapshot(
            tenant_id=request.tenant_id,
            source_system=source_dataset.snapshot.source_system,
            adapter_name=source_dataset.snapshot.adapter_name,
            adapter_version=source_dataset.snapshot.adapter_version,
            as_of_exclusive=request.as_of_exclusive,
            window_start_inclusive=request.window_start_inclusive,
            capabilities=AWARD_NOMINATION_CAPABILITIES,
            is_synthetic_tenant=source_dataset.snapshot.is_synthetic_tenant,
            actors=source_dataset.actors,
            events=source_dataset.events,
            participants=source_dataset.participants,
            relationships=source_dataset.relationships,
            labels=labels,
        )
        dataset = IntegrityDataset(
            snapshot=snapshot,
            actors=source_dataset.actors,
            events=source_dataset.events,
            participants=source_dataset.participants,
            relationships=source_dataset.relationships,
            labels=labels,
        )
        validate_dataset(dataset)
        return dataset
    finally:
        if owns_source:
            source_connection.close()
        if owns_sentinel:
            sentinel_connection.close()
