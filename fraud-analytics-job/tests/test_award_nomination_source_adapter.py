"""T1 contracts and mapping for the source-neutral Award Nomination adapter."""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integrity_data import DatasetValidationError, IntegrityDataset, validate_dataset
from source_adapters.award_nominations.capabilities import (
    AWARD_NOMINATION_CAPABILITIES,
)
from source_adapters.award_nominations.map import map_award_nomination_rows
from source_adapters.award_nominations.extract import fetch_nominations
from source_adapters.award_nominations.live_smoke import summarize_dataset
from source_adapters.contracts import SourceReadRequest


AS_OF = datetime(2026, 9, 17, tzinfo=timezone.utc)


def _request() -> SourceReadRequest:
    return SourceReadRequest(tenant_id=5, as_of_exclusive=AS_OF, window_days=365)


def _tenant(*, synthetic: bool = True) -> dict:
    return {"TenantId": 5, "TenantName": "Synthetics Inc", "IsSynthetic": synthetic}


def _users() -> list[dict]:
    known = datetime(2026, 1, 1)
    return [
        {
            "UserId": 101,
            "TenantId": 5,
            "Title": "Finance",
            "CreatedAt": known,
            "UpdatedAt": known,
        },
        {
            "UserId": 102,
            "TenantId": 5,
            "Title": "IT",
            "CreatedAt": known,
            "UpdatedAt": known,
        },
        {
            "UserId": 103,
            "TenantId": 5,
            "Title": "Legal",
            "CreatedAt": known,
            "UpdatedAt": known,
        },
    ]


def _nomination(*, beneficiary_id: int = 102) -> dict:
    return {
        "NominationId": 7001,
        "NominatorId": 101,
        "BeneficiaryId": beneficiary_id,
        "ApproverId": 103,
        "Amount": 2500,
        "Currency": "USD",
        "NominationDescription": "Resolved a difficult customer incident.",
        "NominationDate": datetime(2026, 8, 15, 12, 0),
        "Status": "Rejected",
        "CategoryId": 8,
        "RejectionActor": "HRBP Review",
        "ApprovedDate": None,
        "PayedDate": None,
        "NominationCreatedAt": datetime(2026, 8, 15, 12, 0),
        "NominationUpdatedAt": datetime(2026, 8, 16, 9, 0),
        "NominatorTenantId": 5,
        "BeneficiaryTenantId": 5,
        "ApproverTenantId": 5,
        "DecisionTenantId": 5,
        "TrainingDisposition": "FRAUD",
        "TrainingDispositionSource": "SYNTHETIC_GROUND_TRUTH",
        "TrainingDispositionMetadataJson": (
            '{"schema_version":3,"confirmed_patterns":["RECIPROCAL","RING"]}'
        ),
        "ReviewedBy": None,
        "ReviewedAt": None,
        "DecisionCreatedAt": datetime(2026, 8, 16, 9, 0),
        "DecisionUpdatedAt": datetime(2026, 8, 16, 9, 0),
    }


def _dataset(*, users=None, nominations=None, synthetic=True) -> IntegrityDataset:
    return map_award_nomination_rows(
        tenant=_tenant(synthetic=synthetic),
        user_rows=_users() if users is None else users,
        nomination_rows=[_nomination()] if nominations is None else nominations,
        request=_request(),
        capabilities=AWARD_NOMINATION_CAPABILITIES,
    )


def test_maps_nomination_to_canonical_snapshot_without_model_output():
    dataset = _dataset()

    assert dataset.snapshot.source_system == "AWARD_NOMINATION"
    assert dataset.snapshot.adapter_version == "1.0.0"
    assert dataset.snapshot.record_counts == {
        "actors": 3,
        "events": 1,
        "participants": 3,
        "relationships": 0,
        "labels": 1,
    }
    assert dataset.events[0].amount == 2500
    assert dataset.events[0].category == "8"
    assert {
        (item.source_role, item.normalized_role)
        for item in dataset.participants
    } == {
        ("NOMINATOR", "INITIATOR"),
        ("BENEFICIARY", "SUBJECT"),
        ("APPROVER", "APPROVER"),
    }
    assert dataset.labels[0].behavior_labels == ("RECIPROCAL", "RING")
    assert len(dataset.snapshot.records_sha256) == 64


def test_snapshot_hash_is_independent_of_source_row_order():
    forward = _dataset()
    reverse = _dataset(users=list(reversed(_users())))

    assert forward.snapshot.records_sha256 == reverse.snapshot.records_sha256
    assert forward.snapshot.snapshot_id == reverse.snapshot.snapshot_id


def test_cross_tenant_or_orphaned_participant_fails_closed():
    with pytest.raises(DatasetValidationError, match="absent from tenant roster"):
        _dataset(nominations=[_nomination(beneficiary_id=999)])


def test_raw_cross_tenant_ownership_fails_before_canonicalization():
    nomination = _nomination()
    nomination["BeneficiaryTenantId"] = 6

    with pytest.raises(ValueError, match="outside tenant 5"):
        _dataset(nominations=[nomination])


def test_synthetic_ground_truth_is_rejected_for_real_tenant():
    with pytest.raises(ValueError, match="non-synthetic tenant"):
        _dataset(synthetic=False)


def test_label_not_known_by_cutoff_is_not_in_snapshot():
    nomination = _nomination()
    nomination["DecisionCreatedAt"] = datetime(2026, 9, 18)

    dataset = _dataset(nominations=[nomination])

    assert dataset.labels == ()
    assert dataset.snapshot.record_counts["labels"] == 0


def test_do_not_use_status_is_dropped_before_canonicalization():
    nomination = _nomination()
    nomination["Status"] = "DO_NOT_USE"

    dataset = _dataset(nominations=[nomination])

    assert dataset.events == ()
    assert dataset.participants == ()
    assert dataset.labels == ()


def test_tampering_breaks_snapshot_validation():
    dataset = _dataset()
    changed_events = list(dataset.events)
    changed_event = copy.copy(changed_events[0])
    object.__setattr__(changed_event, "status", "Paid")
    changed_events[0] = changed_event
    tampered = IntegrityDataset(
        snapshot=dataset.snapshot,
        actors=dataset.actors,
        events=tuple(changed_events),
        participants=dataset.participants,
        relationships=dataset.relationships,
        labels=dataset.labels,
    )

    with pytest.raises(DatasetValidationError, match="hash"):
        validate_dataset(tampered)


class _RecordingCursor:
    def __init__(self):
        self.sql = ""
        self.params = ()
        self.description = []

    def execute(self, sql, *params):
        self.sql = sql
        self.params = params
        self.description = [("NominationId",)]
        return self

    def fetchall(self):
        return []


class _RecordingConnection:
    def __init__(self):
        self.cursor_instance = _RecordingCursor()

    def cursor(self):
        return self.cursor_instance


def test_nomination_extraction_is_source_scoped_and_uses_explicit_cutoffs():
    connection = _RecordingConnection()

    result = fetch_nominations(connection, _request())

    assert result == []
    sql = connection.cursor_instance.sql
    params = connection.cursor_instance.params
    assert "JOIN dbo.Users nominator" in sql
    assert "nominator.TenantId = ?" in sql
    assert "n.NominationDate < ?" in sql
    assert "n.Status <> ?" in sql
    assert "n.NominationDate >= ?" in sql
    assert "dbo.IntegrityDecisionResults" in sql
    assert "GraphPatternFindings" not in sql
    assert "GNN_UserEmbeddings" not in sql
    assert "RfResultJson" not in sql
    assert params[0] == 5
    assert params[1] == datetime(2026, 9, 17)
    assert params[2] == "DO_NOT_USE"
    assert params[3] == datetime(2025, 9, 17)


def test_live_smoke_summary_contains_only_aggregate_diagnostics():
    summary = summarize_dataset(_dataset())

    assert summary["validation"] == "PASSED"
    assert summary["record_counts"]["events"] == 1
    assert summary["label_provenance_counts"] == {
        "SYNTHETIC_GROUND_TRUTH": 1
    }
    assert summary["label_disposition_counts"] == {"FRAUD": 1}
    assert summary["participant_role_counts"] == {
        "APPROVER": 1,
        "BENEFICIARY": 1,
        "NOMINATOR": 1,
    }
    rendered = json.dumps(summary)
    assert "Resolved a difficult customer incident" not in rendered
    assert "Finance" not in rendered
