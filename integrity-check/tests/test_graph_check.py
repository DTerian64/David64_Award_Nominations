import os
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import graph_check

DETAILS = {"nomination_id": 10, "nominator_id": 1, "beneficiary_id": 2, "approver_id": 3}
POLICY = {
    "policy_id": 8, "policy_version": 2, "status": "ACTIVE",
    "scoring_strategy": "MAX_RELEVANT_FINDING",
    "thresholds": {"low": 25, "medium": 50, "high": 75, "critical": 90},
    "detection_window_days": 365, "snapshot_max_age_days": 14,
}


def _snapshot(users=None, *, as_of=None):
    return {
        "snapshot_as_of": as_of or date.today(),
        "snapshot_run_id": "graph-run-1",
        "snapshot_finding_count": len(users or {}),
        "scoring_policy_version": 2,
        "users": users or {},
    }


def _finding(pattern, score, *, roles=None, routing=True, finding_hash="finding-1"):
    return {
        "finding_hash": finding_hash, "pattern_type": pattern,
        "finding_score": score, "severity": "High",
        "nomination_ids": [7, 8], "detail": "Evidence",
        "enabled_for_routing": routing,
        "applicable_roles": roles or ["nominator", "beneficiary"],
        "score_components": {"finding_score": score},
    }


class GraphCheckTests(unittest.TestCase):
    def setUp(self):
        patcher = patch(
            "inference.graph_check.db.get_graph_scoring_policy", return_value=POLICY
        )
        self.addCleanup(patcher.stop)
        self.policy_lookup = patcher.start()

    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_only_nominator_and_beneficiary_participate(self, lookup):
        lookup.return_value = _snapshot({
            1: {"findings": [_finding("Ring", 82, finding_hash="ring-1")]},
            3: {"findings": [_finding("ApproverAffinity", 100, finding_hash="legacy")]},
        })
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertEqual(lookup.call_args.args[1], [1, 2])
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertEqual(result["fraud_score"], 82)
        self.assertEqual(result["affected_user_ids"], [1])
        self.assertNotIn("approver", " ".join(result["warning_flags"]).lower())

    @patch("inference.graph_check.db.get_graph_component_snapshot", return_value=None)
    def test_missing_snapshot_is_no_opinion_not_clean(self, _lookup):
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertFalse(result["model_available"])
        self.assertEqual(result["unavailable_reason"], "NO_SNAPSHOT")

    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_complete_snapshot_with_no_participant_findings_is_clean(self, lookup):
        lookup.return_value = _snapshot()
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertTrue(result["model_available"])
        self.assertEqual(result["risk_level"], "NONE")
        self.assertEqual(result["fraud_score"], 0)

    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_legacy_snapshot_is_not_misreported_as_clean(self, lookup):
        lookup.return_value = {**_snapshot(), "scoring_policy_version": None}
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertFalse(result["model_available"])
        self.assertEqual(result["unavailable_reason"], "LEGACY_SNAPSHOT")

    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_maximum_relevant_continuous_score_wins(self, lookup):
        lookup.return_value = _snapshot({
            1: {"findings": [
                _finding("SuperNominator", 64.25, roles=["nominator"], finding_hash="super"),
                _finding("Desert", 95, roles=["beneficiary"], finding_hash="wrong-role"),
            ]},
            2: {"findings": [
                _finding("HiddenCandidate", 98, roles=["beneficiary"], routing=False, finding_hash="analytics"),
                _finding("Ring", 81.75, finding_hash="ring"),
            ]},
        })
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertEqual(result["fraud_score"], 81.75)
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertEqual(result["winning_finding_hash"], "ring")
        by_hash = {item["finding_hash"]: item for item in result["pattern_findings"]}
        self.assertFalse(by_hash["wrong-role"]["routing_relevant"])
        self.assertFalse(by_hash["analytics"]["routing_relevant"])

    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_stale_snapshot_is_unavailable_by_policy(self, lookup):
        stale_date = date.today() - timedelta(days=15)
        lookup.return_value = _snapshot(as_of=stale_date)
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertFalse(result["model_available"])
        self.assertEqual(result["unavailable_reason"], "STALE_SNAPSHOT")
        self.assertEqual(result["snapshot_age_days"], 15)


if __name__ == "__main__":
    unittest.main()
