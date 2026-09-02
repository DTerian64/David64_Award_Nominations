"""Continuous Graph detector and snapshot-contract regression tests."""

import json

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


def _nomination(nomination_id, nominator, beneficiary, amount=1000, description=""):
    return {
        "NominationId": nomination_id, "NominatorId": nominator,
        "BeneficiaryId": beneficiary, "Amount": amount,
        "Description": description,
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
