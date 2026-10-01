"""Source-neutral projections from canonical records into legacy model inputs."""

from __future__ import annotations

import json

import pandas as pd

from integrity_data import IntegrityDataset


def _event_participants(dataset: IntegrityDataset) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for participant in dataset.participants:
        result.setdefault(participant.event_id, {})[participant.normalized_role] = int(
            participant.actor_id
        )
    return result


def actor_rows(dataset: IntegrityDataset) -> list[dict]:
    """Return the source-neutral actor view used by Graph and GNN builders."""

    return [
        {
            "UserId": int(actor.actor_id),
            "TenantId": actor.tenant_id,
            "FullName": str(
                actor.attributes.get("display_name") or actor.actor_id
            ),
            "ManagerId": (
                int(actor.attributes["manager_actor_id"])
                if actor.attributes.get("manager_actor_id") is not None
                else None
            ),
            "EverActiveBeforeAsOf": bool(
                actor.attributes.get("ever_active_before_as_of", False)
            ),
        }
        for actor in dataset.actors
    ]


def graph_nomination_rows(dataset: IntegrityDataset) -> list[dict]:
    """Return behavioral events eligible for deterministic graph detectors."""

    participants = _event_participants(dataset)
    rows = []
    for event in dataset.events:
        if str(event.status or "") not in {"Pending", "Approved", "Paid"}:
            continue
        roles = participants.get(event.event_id, {})
        if "INITIATOR" not in roles or "SUBJECT" not in roles:
            continue
        rows.append(
            {
                "NominationId": int(event.event_id),
                "NominatorId": roles["INITIATOR"],
                "BeneficiaryId": roles["SUBJECT"],
                "Status": event.status,
                # Canonical monetary values remain Decimal, but the Graph
                # detector contract performs floating-point scoring.
                "Amount": (
                    float(event.amount) if event.amount is not None else None
                ),
                "Description": event.text,
                "CreatedAt": event.occurred_at,
            }
        )
    return rows


def gnn_nomination_rows(dataset: IntegrityDataset) -> list[dict]:
    """Return topology and target candidates for GNN graph construction."""

    participants = _event_participants(dataset)
    labels = {label.event_id: label for label in dataset.labels}
    rows = []
    for event in dataset.events:
        roles = participants.get(event.event_id, {})
        if "INITIATOR" not in roles or "SUBJECT" not in roles:
            continue
        label = labels.get(event.event_id)
        status = str(event.status or "")
        if status not in {"Pending", "Approved", "Paid"} and not (
            label is not None and label.disposition in {"FRAUD", "LEGITIMATE"}
        ):
            continue
        metadata = label.metadata if label is not None else {}
        behavior_eligible = status in {"Pending", "Approved", "Paid"} or (
            status == "Rejected"
            and label is not None
            and label.disposition == "FRAUD"
            and metadata.get("final_route") == "HRBP_REVIEW"
            and metadata.get("review_scope") in {"FRAUD", "FRAUD_AND_SEMANTIC"}
        )
        rows.append(
            {
                "NominationId": int(event.event_id),
                "NominatorId": roles["INITIATOR"],
                "BeneficiaryId": roles["SUBJECT"],
                "Status": event.status,
                "Amount": event.amount,
                "CategoryId": (
                    int(event.category) if event.category is not None else None
                ),
                "CreatedAt": event.occurred_at,
                "IsBehaviorEligible": int(behavior_eligible),
            }
        )
    return rows


def label_frame(dataset: IntegrityDataset) -> pd.DataFrame:
    """Return the existing model-neutral label frame from canonical outcomes."""

    labels = {label.event_id: label for label in dataset.labels}
    rows = []
    for event in dataset.events:
        status = str(event.status or "")
        if status == "PendingHRBPReview" or (
            status == "Rejected"
            and event.attributes.get("rejection_actor")
            == "Fraud Detection (Description)"
        ):
            continue
        label = labels.get(event.event_id)
        disposition = label.disposition if label is not None else None
        provenance = label.provenance if label is not None else None
        eligible = disposition in {"FRAUD", "LEGITIMATE"} and provenance in {
            "HUMAN_INVESTIGATION",
            "RANDOM_AUDIT",
            "SYNTHETIC_GROUND_TRUTH",
        }
        is_fraud = (
            1 if eligible and disposition == "FRAUD"
            else 0 if eligible and disposition == "LEGITIMATE"
            else None
        )
        label_source = (
            "excluded"
            if disposition == "EXCLUDED"
            else "hrbp"
            if provenance in {"HUMAN_INVESTIGATION", "RANDOM_AUDIT"}
            else "synthetic_ground_truth"
            if provenance == "SYNTHETIC_GROUND_TRUTH"
            else "unlabelled"
        )
        metadata = dict(label.metadata) if label is not None else {}
        rows.append(
            {
                "NominationId": int(event.event_id),
                "RiskLevel": None,
                "ConfirmedBy": label.reviewed_by if label is not None else None,
                "ConfirmedAt": label.reviewed_at if label is not None else None,
                "TrainingDisposition": disposition,
                "TrainingDispositionSource": provenance,
                "TrainingDispositionMetadataJson": (
                    json.dumps(metadata, separators=(",", ":")) if metadata else None
                ),
                "ScenarioFamily": metadata.get("scenario_family"),
                "ScenarioVariant": metadata.get("scenario_variant"),
                "IsSyntheticTenant": int(dataset.snapshot.is_synthetic_tenant),
                "IsFraud": is_fraud,
                "LabelSource": label_source,
                "InvalidSyntheticSource": 0,
                "ConfirmedPatterns": (
                    label.behavior_labels if label is not None else ()
                ),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(
            columns=(
                "NominationId",
                "RiskLevel",
                "ConfirmedBy",
                "ConfirmedAt",
                "TrainingDisposition",
                "TrainingDispositionSource",
                "TrainingDispositionMetadataJson",
                "ScenarioFamily",
                "ScenarioVariant",
                "IsSyntheticTenant",
                "IsFraud",
                "LabelSource",
                "InvalidSyntheticSource",
                "ConfirmedPatterns",
            )
        )
    frame["IsFraud"] = pd.to_numeric(frame["IsFraud"], errors="coerce").astype(
        "Int64"
    )
    return frame
