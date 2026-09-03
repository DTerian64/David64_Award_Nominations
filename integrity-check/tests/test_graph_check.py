import os
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import graph_check
from utils import db

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
    def test_all_eight_detectors_remain_visible_but_only_six_can_win(self):
        patterns = ['Ring', 'BipartiteDenseBlock', 'TemporalBurst', 'SuperNominator',
                    'SuperBeneficiary', 'CopyPaste', 'HiddenCandidate', 'Desert']
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                routing = pattern not in ('HiddenCandidate', 'Desert')
                with patch.object(db, 'get_graph_component_snapshot', return_value=_snapshot({
                    2: {'findings': [_finding(pattern, 83.5, routing=routing)]}
                })):
                    result = graph_check.assess_graph(DETAILS, 7)
                self.assertEqual(result['fraud_score'], 83.5 if routing else 0)
                self.assertEqual(result['detector_summary'][0]['count'], 1)
                self.assertEqual(len(result['pattern_findings']), 1)

    def test_invalid_snapshot_is_unavailable_not_clean(self):
        with patch.object(db, 'get_graph_component_snapshot', side_effect=db.InvalidGraphSnapshot('bad evidence')):
            result = graph_check.assess_graph(DETAILS, 7)
        self.assertFalse(result['model_available'])
        self.assertEqual(result['unavailable_reason'], 'INVALID_SNAPSHOT')

    def test_grouping_keeps_every_finding_and_does_not_add_scores(self):
        findings = [_finding('Ring', 80, finding_hash=f'ring-{i}') for i in range(10)]
        findings.append(_finding('CopyPaste', 91, finding_hash='winner'))
        with patch.object(db, 'get_graph_component_snapshot', return_value=_snapshot({1: {'findings': findings}})):
            result = graph_check.assess_graph(DETAILS, 7)
        self.assertEqual(result['fraud_score'], 91)
        self.assertEqual(len(result['pattern_findings']), 11)
        self.assertEqual(result['winning_finding']['finding_hash'], 'winner')
        self.assertEqual(result['detector_summary'][1]['count'], 10)

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

    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_emits_nomination_scoped_start_and_completed_logs(self, lookup):
        lookup.return_value = _snapshot({
            1: {"findings": [_finding("Ring", 82, finding_hash="ring-1")]},
        })

        with self.assertLogs("integrity_check.graph_check", "INFO") as logs:
            graph_check.assess_graph(DETAILS, tenant_id=7)

        self.assertEqual(
            [record.getMessage() for record in logs.records],
            [
                "Graph Analytics assessment starting",
                "Graph Analytics assessment completed",
            ],
        )
        for record in logs.records:
            self.assertEqual(record.nomination_id, 10)
            self.assertEqual(record.tenant_id, 7)

        completed = logs.records[-1]
        self.assertTrue(completed.model_available)
        self.assertEqual(completed.fraud_score, 82)
        self.assertEqual(completed.risk_level, "HIGH")
        self.assertEqual(completed.winning_pattern_type, "Ring")
        self.assertEqual(completed.snapshot_run_id, "graph-run-1")
        self.assertEqual(completed.scoring_policy_version, 2)
        self.assertEqual(completed.finding_count, 1)

    @patch(
        "inference.graph_check.db.get_graph_component_snapshot",
        side_effect=RuntimeError("snapshot query failed"),
    )
    def test_failure_log_is_nomination_scoped_and_completion_is_unavailable(self, _lookup):
        with self.assertLogs("integrity_check.graph_check", "INFO") as logs:
            result = graph_check.assess_graph(DETAILS, tenant_id=7)

        self.assertFalse(result["model_available"])
        self.assertEqual(result["unavailable_reason"], "INFERENCE_FAILED")

        failed = next(
            record for record in logs.records
            if record.getMessage() == "Graph Analytics assessment failed"
        )
        self.assertEqual(failed.nomination_id, 10)
        self.assertEqual(failed.tenant_id, 7)
        self.assertEqual(failed.unavailable_reason, "INFERENCE_FAILED")

        completed = logs.records[-1]
        self.assertEqual(
            completed.getMessage(), "Graph Analytics assessment completed"
        )
        self.assertEqual(completed.nomination_id, 10)
        self.assertFalse(completed.model_available)
        self.assertEqual(completed.unavailable_reason, "INFERENCE_FAILED")


class GraphSnapshotReaderTests(unittest.TestCase):
    def _read(self, evidence):
        cursor = MagicMock()
        cursor.fetchone.return_value = ('AVAILABLE', date.today(), 'run-1', json.dumps({
            'snapshot_schema_version': 1, 'scoring_policy_version': 2, 'finding_count': 3,
        }))
        cursor.fetchall.return_value = [] if evidence == 'MISSING_ROW' else [(1, evidence)]
        context = MagicMock()
        context.__enter__.return_value.cursor.return_value = cursor
        with patch.object(db, '_get_conn', return_value=context):
            result = db.get_graph_component_snapshot(7, [1, 2], {
                'serving_status': 'AVAILABLE', 'serving_as_of': date.today(),
            })
        return result, cursor

    def test_absent_user_in_valid_snapshot_is_clean(self):
        result, cursor = self._read('MISSING_ROW')
        self.assertEqual(result['users'], {})
        self.assertIn('HOLDLOCK', cursor.execute.call_args_list[0].args[0])
        sql = cursor.execute.call_args_list[1].args[0]
        self.assertIn('SELECT UserId, FindingsJson', sql)
        self.assertNotIn('IsInRing', sql)

    def test_invalid_evidence_never_becomes_empty_findings(self):
        for raw in (None, '', '{broken', '{}', 'null', '[]', '[null]', '[{}]'):
            with self.subTest(raw=raw), self.assertRaises(db.InvalidGraphSnapshot):
                self._read(raw)

    def test_valid_evidence_and_rejected_cross_snapshot_metadata(self):
        finding = {**_finding('Ring', 80), 'snapshot_run_id': 'run-1', 'scoring_policy_version': 2}
        result, _ = self._read(json.dumps([finding]))
        self.assertEqual(result['users'][1]['findings'][0]['finding_score'], 80)
        for override in ({'finding_score': float('nan')}, {'enabled_for_routing': 'true'},
                         {'snapshot_run_id': 'old'}, {'scoring_policy_version': 1},
                         {'applicable_roles': ['approver']}):
            with self.subTest(override=override), self.assertRaises(db.InvalidGraphSnapshot):
                self._read(json.dumps([{**finding, **override}]))


if __name__ == "__main__":
    unittest.main()
