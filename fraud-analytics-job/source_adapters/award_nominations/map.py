"""Map Award Nomination rows into canonical integrity records."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

from integrity_data import (
    Actor,
    EventParticipant,
    IntegrityDataset,
    IntegrityEvent,
    OutcomeLabel,
    Relationship,
    as_utc,
    build_snapshot,
    validate_dataset,
)
from source_adapters.contracts import SourceReadRequest

from .source_policy import DROPPED_SOURCE_STATUSES

SOURCE_SYSTEM = "AWARD_NOMINATION"
ADAPTER_NAME = "award_nominations"
ADAPTER_VERSION = "1.0.0"

_HUMAN_PROVENANCE = frozenset({"HUMAN_INVESTIGATION", "RANDOM_AUDIT"})


def _optional_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    return as_utc(value)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _metadata(value: Any, event_id: str) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Nomination {event_id} has invalid TrainingDispositionMetadataJson"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Nomination {event_id} training metadata must be a JSON object"
        )
    return parsed


def _behavior_labels(metadata: Mapping[str, Any], event_id: str) -> tuple[str, ...]:
    values = metadata.get("confirmed_patterns", [])
    if values is None:
        return ()
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError(
            f"Nomination {event_id} confirmed_patterns must be an array of strings"
        )
    return tuple(sorted({value.strip().upper() for value in values if value.strip()}))


def _map_label(
    row: Mapping[str, Any],
    *,
    tenant_id: int,
    is_synthetic_tenant: bool,
    as_of_exclusive: datetime,
) -> OutcomeLabel | None:
    raw_disposition = row.get("TrainingDisposition")
    if raw_disposition is None:
        return None

    event_id = str(row["NominationId"])
    disposition = str(raw_disposition).upper()
    provenance_value = row.get("TrainingDispositionSource")
    if provenance_value is None:
        raise ValueError(f"Nomination {event_id} has a disposition without provenance")
    provenance = str(provenance_value).upper()

    reviewed_at = _optional_utc(row.get("ReviewedAt"))
    decision_created_at = _optional_utc(row.get("DecisionCreatedAt"))
    known_at = reviewed_at if provenance in _HUMAN_PROVENANCE else decision_created_at
    if known_at is None:
        raise ValueError(f"Nomination {event_id} label has no known-at timestamp")
    if known_at >= as_of_exclusive:
        return None

    if provenance == "SYNTHETIC_GROUND_TRUTH" and not is_synthetic_tenant:
        raise ValueError(
            f"Nomination {event_id} uses SYNTHETIC_GROUND_TRUTH on a non-synthetic tenant"
        )

    metadata = _metadata(row.get("TrainingDispositionMetadataJson"), event_id)
    return OutcomeLabel(
        tenant_id=tenant_id,
        source_system=SOURCE_SYSTEM,
        event_id=event_id,
        disposition=disposition,
        provenance=provenance,
        known_at=known_at,
        reviewed_by=row.get("ReviewedBy"),
        reviewed_at=reviewed_at,
        behavior_labels=_behavior_labels(metadata, event_id),
        metadata=metadata,
    )


def map_award_nomination_rows(
    *,
    tenant: Mapping[str, Any],
    user_rows: Iterable[Mapping[str, Any]],
    nomination_rows: Iterable[Mapping[str, Any]],
    request: SourceReadRequest,
    capabilities: frozenset[str],
) -> IntegrityDataset:
    """Create and validate one canonical tenant snapshot."""

    tenant_id = int(tenant["TenantId"])
    if tenant_id != request.tenant_id:
        raise ValueError(
            f"Tenant row {tenant_id} does not match request tenant {request.tenant_id}"
        )
    is_synthetic_tenant = bool(tenant.get("IsSynthetic", 0))
    users = list(user_rows)
    nominations = [
        row
        for row in nomination_rows
        if str(row.get("Status") or "").upper() not in DROPPED_SOURCE_STATUSES
    ]

    foreign_users = sorted(
        int(row["UserId"])
        for row in users
        if int(row["TenantId"]) != tenant_id
    )
    if foreign_users:
        raise ValueError(
            f"Award Nomination adapter received users outside tenant {tenant_id}: "
            f"{foreign_users[:10]}"
        )

    tenant_columns = (
        "NominatorTenantId",
        "BeneficiaryTenantId",
        "ApproverTenantId",
        "DecisionTenantId",
    )
    for row in nominations:
        event_id = str(row["NominationId"])
        for column in tenant_columns:
            value = row.get(column)
            if value is not None and int(value) != tenant_id:
                raise ValueError(
                    f"Nomination {event_id} has {column}={value}, outside tenant "
                    f"{tenant_id}"
                )

    actors = tuple(
        Actor(
            tenant_id=tenant_id,
            source_system=SOURCE_SYSTEM,
            actor_id=str(row["UserId"]),
            attributes={"title": row.get("Title")},
        )
        for row in sorted(users, key=lambda item: int(item["UserId"]))
    )
    # Users.ManagerId is current-state data without relationship history. It is
    # deliberately not mapped as a time-valid relationship. A future adapter
    # version may add hierarchy only when the source can provide valid-from or
    # known-at semantics that are safe for historical feature construction.
    relationships: list[Relationship] = []

    events: list[IntegrityEvent] = []
    participants: list[EventParticipant] = []
    labels: list[OutcomeLabel] = []
    for row in sorted(
        nominations,
        key=lambda item: (as_utc(item["NominationDate"]), int(item["NominationId"])),
    ):
        event_id = str(row["NominationId"])
        occurred_at = as_utc(row["NominationDate"])
        known_at = _optional_utc(row.get("NominationCreatedAt")) or occurred_at
        attributes = {
            "approved_at": _optional_utc(row.get("ApprovedDate")),
            "paid_at": _optional_utc(row.get("PayedDate")),
            "rejection_actor": row.get("RejectionActor"),
            "source_updated_at": _optional_utc(row.get("NominationUpdatedAt")),
        }
        events.append(
            IntegrityEvent(
                tenant_id=tenant_id,
                source_system=SOURCE_SYSTEM,
                event_id=event_id,
                event_type="NOMINATION",
                occurred_at=occurred_at,
                known_at=known_at,
                amount=_decimal(row.get("Amount")),
                currency=row.get("Currency"),
                category=(
                    str(row["CategoryId"])
                    if row.get("CategoryId") is not None
                    else None
                ),
                text=row.get("NominationDescription"),
                status=row.get("Status"),
                attributes=attributes,
            )
        )

        role_bindings = (
            ("NOMINATOR", "INITIATOR", row.get("NominatorId")),
            ("BENEFICIARY", "SUBJECT", row.get("BeneficiaryId")),
            ("APPROVER", "APPROVER", row.get("ApproverId")),
        )
        for source_role, normalized_role, actor_id in role_bindings:
            if actor_id is None:
                continue
            participants.append(
                EventParticipant(
                    tenant_id=tenant_id,
                    source_system=SOURCE_SYSTEM,
                    event_id=event_id,
                    actor_id=str(actor_id),
                    source_role=source_role,
                    normalized_role=normalized_role,
                )
            )

        label = _map_label(
            row,
            tenant_id=tenant_id,
            is_synthetic_tenant=is_synthetic_tenant,
            as_of_exclusive=request.as_of_exclusive,
        )
        if label is not None:
            labels.append(label)

    actor_tuple = tuple(actors)
    event_tuple = tuple(events)
    participant_tuple = tuple(participants)
    relationship_tuple = tuple(
        sorted(relationships, key=lambda item: item.relationship_id)
    )
    label_tuple = tuple(sorted(labels, key=lambda item: item.event_id))
    snapshot = build_snapshot(
        tenant_id=tenant_id,
        source_system=SOURCE_SYSTEM,
        adapter_name=ADAPTER_NAME,
        adapter_version=ADAPTER_VERSION,
        as_of_exclusive=request.as_of_exclusive,
        window_start_inclusive=request.window_start_inclusive,
        capabilities=capabilities,
        is_synthetic_tenant=is_synthetic_tenant,
        actors=actor_tuple,
        events=event_tuple,
        participants=participant_tuple,
        relationships=relationship_tuple,
        labels=label_tuple,
    )
    dataset = IntegrityDataset(
        snapshot=snapshot,
        actors=actor_tuple,
        events=event_tuple,
        participants=participant_tuple,
        relationships=relationship_tuple,
        labels=label_tuple,
    )
    validate_dataset(dataset)
    return dataset
