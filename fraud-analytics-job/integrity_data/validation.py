"""Fail-closed validation for canonical integrity datasets."""

from __future__ import annotations

from collections import Counter

from .contracts import IntegrityDataset, as_utc
from .snapshot import compute_records_sha256

ALLOWED_DISPOSITIONS = frozenset({"FRAUD", "LEGITIMATE", "EXCLUDED"})
ALLOWED_PROVENANCE = frozenset(
    {"HUMAN_INVESTIGATION", "RANDOM_AUDIT", "SYNTHETIC_GROUND_TRUTH"}
)


class DatasetValidationError(ValueError):
    """Canonical data violates isolation, time, identity, or provenance rules."""


def _duplicates(values: list[str]) -> list[str]:
    return sorted(key for key, count in Counter(values).items() if count > 1)


def validate_dataset(dataset: IntegrityDataset) -> None:
    snapshot = dataset.snapshot
    as_of = as_utc(snapshot.as_of_exclusive)

    actor_ids = [actor.actor_id for actor in dataset.actors]
    event_ids = [event.event_id for event in dataset.events]
    duplicate_actors = _duplicates(actor_ids)
    duplicate_events = _duplicates(event_ids)
    if duplicate_actors:
        raise DatasetValidationError(f"Duplicate actor ids: {duplicate_actors[:10]}")
    if duplicate_events:
        raise DatasetValidationError(f"Duplicate event ids: {duplicate_events[:10]}")

    actor_set = set(actor_ids)
    event_set = set(event_ids)
    collections = (
        dataset.actors,
        dataset.events,
        dataset.participants,
        dataset.relationships,
        dataset.labels,
    )
    for records in collections:
        for record in records:
            if record.tenant_id != snapshot.tenant_id:
                raise DatasetValidationError(
                    f"Record tenant {record.tenant_id} does not match snapshot tenant "
                    f"{snapshot.tenant_id}"
                )
            if record.source_system != snapshot.source_system:
                raise DatasetValidationError(
                    f"Record source {record.source_system!r} does not match snapshot "
                    f"source {snapshot.source_system!r}"
                )

    for event in dataset.events:
        if as_utc(event.occurred_at) >= as_of:
            raise DatasetValidationError(
                f"Event {event.event_id} is not before exclusive cutoff {as_of.isoformat()}"
            )
        if as_utc(event.known_at) >= as_of:
            raise DatasetValidationError(
                f"Event {event.event_id} was not known at the snapshot cutoff"
            )

    participant_keys: list[str] = []
    for participant in dataset.participants:
        if participant.actor_id not in actor_set:
            raise DatasetValidationError(
                f"Participant actor {participant.actor_id} is absent from tenant roster"
            )
        if participant.event_id not in event_set:
            raise DatasetValidationError(
                f"Participant event {participant.event_id} is absent from event roster"
            )
        participant_keys.append(
            "|".join(
                (
                    participant.event_id,
                    participant.actor_id,
                    participant.source_role,
                )
            )
        )
    duplicate_participants = _duplicates(participant_keys)
    if duplicate_participants:
        raise DatasetValidationError(
            f"Duplicate event participants: {duplicate_participants[:10]}"
        )

    for relationship in dataset.relationships:
        if relationship.source_actor_id not in actor_set:
            raise DatasetValidationError(
                f"Relationship source actor {relationship.source_actor_id} is absent"
            )
        if relationship.target_actor_id not in actor_set:
            raise DatasetValidationError(
                f"Relationship target actor {relationship.target_actor_id} is absent"
            )
        if as_utc(relationship.known_at) >= as_of:
            raise DatasetValidationError(
                f"Relationship {relationship.relationship_id} was not known at cutoff"
            )

    label_events: list[str] = []
    for label in dataset.labels:
        if label.event_id not in event_set:
            raise DatasetValidationError(
                f"Label event {label.event_id} is absent from event roster"
            )
        if label.disposition not in ALLOWED_DISPOSITIONS:
            raise DatasetValidationError(
                f"Unsupported disposition {label.disposition!r} for event {label.event_id}"
            )
        if label.provenance not in ALLOWED_PROVENANCE:
            raise DatasetValidationError(
                f"Unsupported label provenance {label.provenance!r} for event "
                f"{label.event_id}"
            )
        if as_utc(label.known_at) >= as_of:
            raise DatasetValidationError(
                f"Label for event {label.event_id} was not known before cutoff"
            )
        if (
            label.provenance == "SYNTHETIC_GROUND_TRUTH"
            and not snapshot.is_synthetic_tenant
        ):
            raise DatasetValidationError(
                "SYNTHETIC_GROUND_TRUTH is permitted only for a synthetic tenant"
            )
        if label.provenance in {"HUMAN_INVESTIGATION", "RANDOM_AUDIT"}:
            if label.reviewed_by is None or label.reviewed_at is None:
                raise DatasetValidationError(
                    f"Human label for event {label.event_id} lacks reviewer evidence"
                )
        label_events.append(label.event_id)
    duplicate_labels = _duplicates(label_events)
    if duplicate_labels:
        raise DatasetValidationError(
            f"Multiple outcome labels for events: {duplicate_labels[:10]}"
        )

    observed_counts = {
        "actors": len(dataset.actors),
        "events": len(dataset.events),
        "participants": len(dataset.participants),
        "relationships": len(dataset.relationships),
        "labels": len(dataset.labels),
    }
    if dict(snapshot.record_counts) != observed_counts:
        raise DatasetValidationError(
            f"Snapshot counts {dict(snapshot.record_counts)} do not match records "
            f"{observed_counts}"
        )

    observed_hash = compute_records_sha256(
        actors=dataset.actors,
        events=dataset.events,
        participants=dataset.participants,
        relationships=dataset.relationships,
        labels=dataset.labels,
    )
    if snapshot.records_sha256 != observed_hash:
        raise DatasetValidationError(
            "Snapshot hash does not match canonical records"
        )
