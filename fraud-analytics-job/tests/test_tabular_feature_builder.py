"""Canonical-to-Tabular-v1 feature builder tests."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feature_builders.tabular.award_nomination_tabular_v1 import (
    AWARD_NOMINATION_TABULAR_V1_SCHEMA,
    AwardNominationTabularV1FeatureBuilder,
    build_nomination_frame,
    extract_features,
)
from source_adapters.award_nominations.capabilities import (
    AWARD_NOMINATION_CAPABILITIES,
)
from source_adapters.award_nominations.map import map_award_nomination_rows
from source_adapters.contracts import SourceReadRequest


AS_OF = datetime(2026, 9, 17, tzinfo=timezone.utc)


class _DeterministicEncoder:
    def encode(self, descriptions, **_kwargs):
        rows = []
        for position, description in enumerate(descriptions, start=1):
            vector = np.array(
                [
                    float(position),
                    float(len(description) % 7 + 1),
                    float(sum(map(ord, description)) % 11 + 1),
                ]
            )
            rows.append(vector / np.linalg.norm(vector))
        return np.vstack(rows)


def _users():
    known_at = datetime(2026, 1, 1)
    return [
        {
            "UserId": user_id,
            "TenantId": 5,
            "Title": title,
            "CreatedAt": known_at,
            "UpdatedAt": known_at,
        }
        for user_id, title in ((101, "Finance"), (102, "IT"), (103, "Legal"))
    ]


def _nomination(
    nomination_id,
    *,
    day,
    nominator,
    beneficiary,
    amount,
    status="Pending",
    rejection_actor=None,
    disposition="LEGITIMATE",
):
    occurred_at = datetime(2026, 8, day, 12, 0)
    return {
        "NominationId": nomination_id,
        "NominatorId": nominator,
        "BeneficiaryId": beneficiary,
        "ApproverId": 103,
        "Amount": amount,
        "Currency": "USD",
        "NominationDescription": f"Nomination {nomination_id} helped me meet my deadline.",
        "NominationDate": occurred_at,
        "Status": status,
        "CategoryId": 8 + nomination_id % 2,
        "RejectionActor": rejection_actor,
        "ApprovedDate": None,
        "PayedDate": None,
        "NominationCreatedAt": occurred_at,
        "NominationUpdatedAt": occurred_at + timedelta(hours=1),
        "NominatorTenantId": 5,
        "BeneficiaryTenantId": 5,
        "ApproverTenantId": 5,
        "DecisionTenantId": 5,
        "TrainingDisposition": disposition,
        "TrainingDispositionSource": "SYNTHETIC_GROUND_TRUTH",
        "TrainingDispositionMetadataJson": "{}",
        "ReviewedBy": None,
        "ReviewedAt": None,
        "DecisionCreatedAt": occurred_at + timedelta(hours=1),
        "DecisionUpdatedAt": occurred_at + timedelta(hours=1),
    }


def _dataset():
    nominations = [
        _nomination(1, day=1, nominator=101, beneficiary=102, amount=100),
        _nomination(2, day=2, nominator=102, beneficiary=101, amount=300),
        _nomination(
            3,
            day=3,
            nominator=101,
            beneficiary=102,
            amount=500,
            status="Rejected",
            rejection_actor="HRBP Review",
            disposition="FRAUD",
        ),
        _nomination(
            4,
            day=4,
            nominator=101,
            beneficiary=102,
            amount=700,
            status="PendingHRBPReview",
        ),
        _nomination(
            5,
            day=5,
            nominator=101,
            beneficiary=102,
            amount=900,
            status="Rejected",
            rejection_actor="Fraud Detection (Description)",
        ),
        _nomination(
            6,
            day=6,
            nominator=101,
            beneficiary=102,
            amount=1100,
            status="DO_NOT_USE",
        ),
    ]
    return map_award_nomination_rows(
        tenant={"TenantId": 5, "TenantName": "Synthetics Inc", "IsSynthetic": 1},
        user_rows=_users(),
        nomination_rows=nominations,
        request=SourceReadRequest(tenant_id=5, as_of_exclusive=AS_OF),
        capabilities=AWARD_NOMINATION_CAPABILITIES,
    )


def _legacy_frame():
    frame = pd.DataFrame(
        [
            {
                "NominationId": 1,
                "NominatorId": 101,
                "BeneficiaryId": 102,
                "Amount": 100.0,
                "Currency": "USD",
                "NominationDescription": "Nomination 1 helped me meet my deadline.",
                "NominationDate": datetime(2026, 8, 1, 12),
                "Status": "Pending",
                "CategoryId": 9,
                "IsFraud": 0,
                "LabelSource": "synthetic_ground_truth",
                "TrainingDisposition": "LEGITIMATE",
            },
            {
                "NominationId": 2,
                "NominatorId": 102,
                "BeneficiaryId": 101,
                "Amount": 300.0,
                "Currency": "USD",
                "NominationDescription": "Nomination 2 helped me meet my deadline.",
                "NominationDate": datetime(2026, 8, 2, 12),
                "Status": "Pending",
                "CategoryId": 8,
                "IsFraud": 0,
                "LabelSource": "synthetic_ground_truth",
                "TrainingDisposition": "LEGITIMATE",
            },
            {
                "NominationId": 3,
                "NominatorId": 101,
                "BeneficiaryId": 102,
                "Amount": 500.0,
                "Currency": "USD",
                "NominationDescription": "Nomination 3 helped me meet my deadline.",
                "NominationDate": datetime(2026, 8, 3, 12),
                "Status": "Rejected",
                "CategoryId": 9,
                "IsFraud": 1,
                "LabelSource": "synthetic_ground_truth",
                "TrainingDisposition": "FRAUD",
            },
        ]
    ).astype({"IsFraud": "Int64"})
    frame["RejectionActor"] = None
    frame.loc[frame["Status"] == "Rejected", "RejectionActor"] = "HRBP Review"
    return frame


def test_nomination_frame_applies_source_and_rf_policies_separately():
    dataset = _dataset()
    frame = build_nomination_frame(dataset)

    assert {event.event_id for event in dataset.events} == {"1", "2", "3", "4", "5"}
    assert frame["NominationId"].tolist() == [1, 2, 3]
    assert frame["LabelSource"].tolist() == [
        "synthetic_ground_truth",
        "synthetic_ground_truth",
        "synthetic_ground_truth",
    ]
    assert frame["IsFraud"].tolist() == [0, 0, 1]


def test_tabular_v1_schema_contains_expected_semantic_and_cyclic_features():
    assert {
        "DescriptionCosineSim",
        "DescriptionEmbDistance",
        "TransactionalPhraseScore",
        "DayOfWeekSin",
        "DayOfWeekCos",
        "MonthSin",
        "MonthCos",
    }.issubset(AWARD_NOMINATION_TABULAR_V1_SCHEMA.feature_columns)


def test_builder_emits_valid_ordered_non_serving_feature_dataset():
    result = AwardNominationTabularV1FeatureBuilder().build(
        _dataset(), embed_model=_DeterministicEncoder()
    )

    assert result.source_snapshot_id == _dataset().snapshot.snapshot_id
    assert list(result.features.columns) == list(
        AWARD_NOMINATION_TABULAR_V1_SCHEMA.feature_columns
    )
    assert result.target.tolist() == [0, 0, 1]
    assert result.diagnostics == {
        "source_event_count": 5,
        "eligible_event_count": 3,
        "excluded_by_rf_policy": 2,
    }
    result.validate()


def test_tabular_v1_cyclic_coordinates_preserve_calendar_closeness():
    source = _legacy_frame()
    source.loc[0, "NominationDate"] = datetime(2026, 1, 4)  # Sunday / January
    source.loc[1, "NominationDate"] = datetime(2026, 1, 5)  # Monday / January
    source.loc[2, "NominationDate"] = datetime(2025, 12, 31)  # Wednesday / December
    source["DescriptionCosineSim"] = 0.0
    source["DescriptionEmbDistance"] = 1.0

    engineered, _, _ = extract_features(source)

    sunday = engineered.loc[0, ["DayOfWeekSin", "DayOfWeekCos"]].to_numpy(float)
    monday = engineered.loc[1, ["DayOfWeekSin", "DayOfWeekCos"]].to_numpy(float)
    wednesday = engineered.loc[2, ["DayOfWeekSin", "DayOfWeekCos"]].to_numpy(float)
    assert np.linalg.norm(sunday - monday) < np.linalg.norm(sunday - wednesday)

    january = engineered.loc[0, ["MonthSin", "MonthCos"]].to_numpy(float)
    december = engineered.loc[2, ["MonthSin", "MonthCos"]].to_numpy(float)
    assert np.linalg.norm(january - december) < 1.0
