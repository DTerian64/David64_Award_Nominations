"""Continuous Graph detector and snapshot-contract regression tests."""

import json
from datetime import date, timedelta

from modeling import graph_analytics as graph


POLICY = {
    "version": 3,
    "strategy": "MAX_RELEVANT_FINDING",
    "thresholds": {"low": 25, "medium": 50, "high": 75, "critical": 90},
    "patterns": {
        "Ring": {
            "base_score": 35, "minimum_score": 0, "maximum_score": 100,
            "enabled_for_routing": True,
            "applicable_roles": ["nominator", "beneficiary"],
            "parameters": {
                "amount_reference": 10000, "exposure_weight": 35,
                "repeat_weight": 15, "compactness_weight": 15,
            },
        },
        "BipartiteDenseBlock": {
            "base_score": 30, "minimum_score": 0, "maximum_score": 100,
            "enabled_for_routing": True,
            "applicable_roles": ["nominator", "beneficiary"],
            "parameters": {
                "minimum_side_size": 2, "minimum_large_side_size": 3,
                "minimum_shared_neighbors": 2, "overlap_threshold": 0.6,
                "minimum_density": 0.65, "minimum_edges": 6,
                "density_weight": 20, "overlap_weight": 15,
                "exclusivity_weight": 10, "repeat_weight": 10,
                "compactness_weight": 5, "exposure_weight": 10,
                "repeat_reference": 2, "compactness_reference_days": 14,
                "amount_reference": 10000,
            },
        },
        "TemporalBurst": {
            "base_score": 25, "minimum_score": 0, "maximum_score": 100,
            "enabled_for_routing": True,
            "applicable_roles": ["nominator", "beneficiary"],
            "parameters": {
                "burst_window_days": 3, "minimum_baseline_days": 21,
                "minimum_nominations": 8, "standard_deviations": 3,
                "overlap_suppression": 0.6, "count_reference": 20,
                "excess_weight": 25, "volume_weight": 15,
                "participant_concentration_weight": 15,
                "temporal_compactness_weight": 10,
                "exposure_weight": 10, "amount_reference": 10000,
            },
        },
        "SuperBeneficiary": {
            "base_score": 20, "minimum_score": 0, "maximum_score": 100,
            "enabled_for_routing": False,
            "applicable_roles": ["beneficiary"],
            "parameters": {
                "minimum_count": 5, "minimum_unique_nominators": 4,
                "standard_deviations": 2, "median_multiplier": 3,
                "unique_reference": 10, "compactness_reference_days": 14,
                "amount_reference": 10000, "excess_weight": 20,
                "breadth_weight": 20, "repeat_concentration_weight": 10,
                "compactness_weight": 15, "exposure_weight": 15,
            },
        },
    },
}


class _Cursor:
    def __init__(self):
        self.executions = []
        self.batches = []

    def execute(self, sql, params=None):
        self.executions.append((sql, params))
        return self

    def executemany(self, sql, rows):
        self.batches.append((sql, list(rows)))
        return self


class _Connection:
    def __init__(self):
        self.cursor_value = _Cursor()
        self.committed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True


def _nomination(
    nomination_id, nominator, beneficiary, amount=1000, description="",
    created_at=None,
):
    return {
        "NominationId": nomination_id, "NominatorId": nominator,
        "BeneficiaryId": beneficiary, "Amount": amount,
        "Description": description, "CreatedAt": created_at,
    }


def test_ring_score_increases_with_financial_exposure():
    users = [{"UserId": value, "FullName": str(value)} for value in (1, 2, 3)]
    low = graph.detect_rings([
        _nomination(1, 1, 2, 100), _nomination(2, 2, 3, 100),
        _nomination(3, 3, 1, 100),
    ], users, 7, "low", 3, POLICY)[0]
    high = graph.detect_rings([
        _nomination(4, 1, 2, 4000), _nomination(5, 2, 3, 4000),
        _nomination(6, 3, 1, 4000),
    ], users, 7, "high", 3, POLICY)[0]

    assert high["FindingScore"] > low["FindingScore"]
    assert high["Severity"] == "Critical"
    assert high["ScoringPolicyVersion"] == 3
    components = json.loads(high["ScoreComponentsJson"])
    assert components["signals"]["exposure"] == 1.0


def test_super_beneficiary_requires_broad_support_and_scores_continuously():
    start = date(2026, 8, 1)
    nominations = [
        _nomination(index, index, 99, 500, created_at=start + timedelta(days=index % 3))
        for index in range(1, 16)
    ]
    nominations.extend(
        _nomination(100 + index, 200 + index, 300 + index, 100, created_at=start)
        for index in range(10)
    )

    findings = graph.detect_super_beneficiaries(
        nominations, tenant_id=7, run_id="beneficiary", policy=POLICY
    )

    assert len(findings) == 1
    assert json.loads(findings[0]["AffectedUsers"]) == [99]
    assert not findings[0]["EnabledForRouting"]
    components = json.loads(findings[0]["ScoreComponentsJson"])
    assert components["signals"]["breadth"] == 1.0
    assert components["signals"]["compactness"] > 0.8


def test_temporal_burst_uses_robust_baseline_and_suppresses_overlap():
    start = date(2026, 7, 1)
    nominations = [
        _nomination(
            index + 1, 100 + index, 200 + index, 100,
            created_at=start + timedelta(days=index),
        )
        for index in range(30)
    ]
    nominations.extend(
        _nomination(
            1000 + index, 1 + (index % 3), 50 + (index % 2), 500,
            created_at=start + timedelta(days=20),
        )
        for index in range(15)
    )

    findings = graph.detect_temporal_bursts(
        nominations, tenant_id=7, run_id="burst", policy=POLICY
    )

    assert len(findings) == 1
    assert len(json.loads(findings[0]["NominationIds"])) >= 15
    components = json.loads(findings[0]["ScoreComponentsJson"])
    assert components["signals"]["participant_concentration"] > 0.15
    assert components["signals"]["temporal_compactness"] > 0.8


def test_bipartite_dense_block_finds_many_to_few_campaign():
    start = date(2026, 8, 1)
    nominations = [
        _nomination(
            index + 1, nominator, beneficiary, 1000,
            created_at=start + timedelta(days=index % 2),
        )
        for index, (nominator, beneficiary) in enumerate(
            (left, right)
            for left in (1, 2, 3)
            for right in (10, 11)
        )
    ]

    findings = graph.detect_bipartite_dense_blocks(
        nominations, tenant_id=7, run_id="block", policy=POLICY
    )

    assert len(findings) == 1
    assert json.loads(findings[0]["AffectedUsers"]) == [1, 2, 3, 10, 11]
    components = json.loads(findings[0]["ScoreComponentsJson"])
    assert components["signals"]["density"] == 1.0
    assert components["signals"]["overlap"] == 1.0


def test_clean_run_uses_status_as_marker_and_writes_no_user_rows():
    connection = _Connection()
    graph._populate_graph_flag_snapshots(
        connection, tenant_id=7, findings=[],
        as_of_date="2026-08-31", run_id="run-clean",
    )
    sql = "\n".join(statement for statement, _params in connection.cursor_value.executions)
    assert "DELETE FROM dbo.UserGraphFlags" in sql
    assert "GraphSnapshotRuns" not in sql
    assert "ApproverPairFlags" not in sql
    assert connection.cursor_value.batches == []
    assert not connection.committed


def test_snapshot_carries_score_and_ignores_legacy_approver_finding():
    connection = _Connection()
    ring = graph._finding(
        7, "run-1", "Ring", "High", [1, 2, 3], [11, 12, 13],
        "Three-person directed ring", total_amount=6000, policy=POLICY,
        signals={"exposure": 0.6, "repeat": 0.34, "compactness": 1},
    )
    approver = graph._finding(
        7, "run-1", "ApproverAffinity", "Critical", [4, 5], [20],
        "Historical approver finding",
    )
    graph._populate_graph_flag_snapshots(
        connection, 7, [ring, approver], "2026-08-31", "run-1"
    )
    assert len(connection.cursor_value.batches) == 1
    statement, rows = connection.cursor_value.batches[0]
    assert "FindingsJson" in statement
    assert len(rows) == 3
    evidence = json.loads(rows[0][-1])[0]
    assert evidence["finding_score"] == ring["FindingScore"]
    assert evidence["scoring_policy_version"] == 3
    assert all(row[1] not in {4, 5} for row in rows)
