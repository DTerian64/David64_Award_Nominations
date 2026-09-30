from datetime import datetime, timezone

import pytest

from integrity_sentinel.outcomes import load_outcome_labels


class _Cursor:
    description = [
        (name,)
        for name in (
            "NominationId",
            "TrainingDisposition",
            "TrainingDispositionSource",
            "TrainingDispositionMetadataJson",
            "ReviewedBy",
            "ReviewedAt",
            "CreatedAt",
            "UpdatedAt",
            "FinalRoute",
            "ReviewScope",
        )
    ]

    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def execute(self, sql, *_params):
        self.sql = sql
        return self

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.cursor_value = _Cursor(rows)

    def cursor(self):
        return self.cursor_value


def _row():
    known = datetime(2026, 9, 1)
    return (
        7,
        "FRAUD",
        "SYNTHETIC_GROUND_TRUTH",
        '{"confirmed_patterns":["RING"]}',
        None,
        None,
        known,
        known,
        "HRBP_REVIEW",
        "FRAUD",
    )


def test_outcomes_are_loaded_from_sentinel_without_a_source_join():
    connection = _Connection([_row()])
    labels = load_outcome_labels(
        connection,
        tenant_id=5,
        source_system="AWARD_NOMINATION",
        event_ids=("7",),
        as_of_exclusive=datetime(2026, 9, 30, tzinfo=timezone.utc),
        is_synthetic_tenant=True,
    )
    assert labels[0].behavior_labels == ("RING",)
    assert labels[0].metadata["final_route"] == "HRBP_REVIEW"
    assert "integrity.IntegrityDecisionResults" in connection.cursor_value.sql
    assert "dbo." not in connection.cursor_value.sql


def test_synthetic_outcome_is_rejected_for_a_real_tenant():
    with pytest.raises(ValueError, match="synthetic ground truth"):
        load_outcome_labels(
            _Connection([_row()]),
            tenant_id=5,
            source_system="AWARD_NOMINATION",
            event_ids=("7",),
            as_of_exclusive=datetime(2026, 9, 30, tzinfo=timezone.utc),
            is_synthetic_tenant=False,
        )
