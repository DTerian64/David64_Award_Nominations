"""Canonical reviewed outcomes stored by Integrity Sentinel."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Mapping

from integrity_data import OutcomeLabel, as_utc


_HUMAN_PROVENANCE = frozenset({"HUMAN_INVESTIGATION", "RANDOM_AUDIT"})


def _metadata(value: Any, event_id: str) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Event {event_id} has invalid outcome metadata") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"Event {event_id} outcome metadata must be an object")
    return parsed


def load_outcome_labels(
    connection: Any,
    *,
    tenant_id: int,
    source_system: str,
    event_ids: Iterable[str],
    as_of_exclusive: datetime,
    is_synthetic_tenant: bool,
) -> tuple[OutcomeLabel, ...]:
    """Load model-neutral outcomes without joining to a source database."""

    included_ids = {str(value) for value in event_ids}
    if not included_ids:
        return ()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT NominationId, TrainingDisposition, TrainingDispositionSource,
               TrainingDispositionMetadataJson, ReviewedBy, ReviewedAt,
               CreatedAt, UpdatedAt, FinalRoute, ReviewScope
        FROM integrity.IntegrityDecisionResults
        WHERE TenantId = ?
        """,
        tenant_id,
    )
    columns = [column[0] for column in cursor.description]
    labels: list[OutcomeLabel] = []
    cutoff = as_utc(as_of_exclusive)
    for raw in cursor.fetchall():
        row = dict(zip(columns, raw, strict=True))
        event_id = str(row["NominationId"])
        if event_id not in included_ids or row.get("TrainingDisposition") is None:
            continue
        disposition = str(row["TrainingDisposition"]).upper()
        provenance = str(row.get("TrainingDispositionSource") or "").upper()
        if not provenance:
            raise ValueError(f"Event {event_id} has a disposition without provenance")
        reviewed_at = (
            as_utc(row["ReviewedAt"]) if row.get("ReviewedAt") is not None else None
        )
        created_at = as_utc(row["CreatedAt"])
        known_at = reviewed_at if provenance in _HUMAN_PROVENANCE else created_at
        if known_at >= cutoff:
            continue
        if provenance == "SYNTHETIC_GROUND_TRUTH" and not is_synthetic_tenant:
            raise ValueError(
                f"Event {event_id} uses synthetic ground truth on a real tenant"
            )
        metadata = _metadata(row.get("TrainingDispositionMetadataJson"), event_id)
        metadata["final_route"] = row.get("FinalRoute")
        metadata["review_scope"] = row.get("ReviewScope")
        raw_patterns = metadata.get("confirmed_patterns") or []
        if not isinstance(raw_patterns, list) or not all(
            isinstance(value, str) for value in raw_patterns
        ):
            raise ValueError(f"Event {event_id} confirmed_patterns must be strings")
        normalized_patterns = tuple(
            sorted(
                {
                    value.strip().upper()
                    for value in raw_patterns
                    if value.strip()
                }
            )
        )
        if metadata.get("generator_version") == "synthetics-inc-v4.0":
            scenario = str(metadata.get("scenario_family") or "").upper()
            if disposition == "FRAUD" and normalized_patterns != (scenario,):
                raise ValueError(
                    f"Event {event_id} synthetic fraud pattern must match its scenario"
                )
            if disposition == "LEGITIMATE" and (
                normalized_patterns or scenario != "LEGITIMATE"
            ):
                raise ValueError(
                    f"Event {event_id} synthetic legitimate label is inconsistent"
                )
        labels.append(
            OutcomeLabel(
                tenant_id=tenant_id,
                source_system=source_system,
                event_id=event_id,
                disposition=disposition,
                provenance=provenance,
                known_at=known_at,
                reviewed_by=row.get("ReviewedBy"),
                reviewed_at=reviewed_at,
                behavior_labels=normalized_patterns,
                metadata=metadata,
            )
        )
    return tuple(sorted(labels, key=lambda item: item.event_id))
