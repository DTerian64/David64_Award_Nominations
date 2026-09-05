import os
import gzip
import hashlib
import json
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import graph_check
from integrity_engine import GraphInferenceSnapshot, RingEvaluation
from utils import db

DETAILS = {
    "nomination_id": 10, "nominator_id": 1, "beneficiary_id": 2,
    "approver_id": 3, "amount": 1000,
    "nomination_date": datetime(2026, 9, 4, tzinfo=timezone.utc),
}
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


def _inference_snapshot():
    return GraphInferenceSnapshot(
        tenant_id=7, run_id="graph-run-1", policy_version=2,
        generated_at=DETAILS["nomination_date"], window_days=365,
        scoring_policy={
            "thresholds": POLICY["thresholds"],
            "patterns": {"Ring": {
                "enabled": True, "enabled_for_routing": True,
                "base_score": 35, "minimum_score": 0, "maximum_score": 100,
                "parameters": {},
            }},
        },
        nominations=(),
    )


def _candidate_ring(score=82):
    return RingEvaluation(
        detector="Ring", evaluation_mode="CANDIDATE_EDGE",
        evidence_scope="CURRENT_NOMINATION", score=score, severity="HIGH",
        score_components={"finding_score": score},
        affected_user_ids=(1, 2, 4), supporting_nomination_ids=(7, 8, 10),
        total_amount=3000, candidate_nomination_id=10,
        path_user_ids=(2, 4, 1, 2), paths_considered=1, states_visited=4,
        states_generated=4,
    )


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
                expected = 83.5 if routing and pattern != 'Ring' else 0
                self.assertEqual(result['fraud_score'], expected)
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
        self.assertEqual(result['winning_pattern_count'], 1)
        self.assertEqual(result['warning_flags'], ['[Graph] nominator: CopyPaste (91.00, CRITICAL)'])

    def test_historical_rings_are_context_only(self):
        findings = [
            _finding('Ring', score, finding_hash=f'ring-{score}')
            for score in (88.2, 84.29, 60.01)
        ]
        with patch.object(db, 'get_graph_component_snapshot', return_value=_snapshot({1: {'findings': findings}})):
            result = graph_check.assess_graph(DETAILS, 7)
        self.assertIsNone(result['winning_pattern_type'])
        self.assertEqual(result['winning_pattern_count'], 0)
        self.assertEqual(result['warning_flags'], [])
        self.assertEqual(len(result['pattern_findings']), 3)
        self.assertTrue(all(
            item['evidence_scope'] == 'NOMINATOR_HISTORY'
            and not item['routing_relevant']
            for item in result['pattern_findings']
        ))

    def setUp(self):
        patcher = patch(
            "inference.graph_check.db.get_graph_scoring_policy", return_value=POLICY
        )
        self.addCleanup(patcher.stop)
        self.policy_lookup = patcher.start()
        snapshot_patcher = patch(
            "inference.graph_check._load_inference_snapshot",
            return_value=_inference_snapshot(),
        )
        self.addCleanup(snapshot_patcher.stop)
        self.snapshot_loader = snapshot_patcher.start()

    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_only_nominator_and_beneficiary_participate(self, lookup):
        lookup.return_value = _snapshot({
            1: {"findings": [_finding("Ring", 82, finding_hash="ring-1")]},
            3: {"findings": [_finding("ApproverAffinity", 100, finding_hash="legacy")]},
        })
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertEqual(lookup.call_args.args[1], [1, 2])
        self.assertEqual(result["risk_level"], "NONE")
        self.assertEqual(result["fraud_score"], 0)
        self.assertEqual(result["affected_user_ids"], [])
        self.assertNotIn("approver", " ".join(result["warning_flags"]).lower())

    @patch("inference.graph_check.evaluate_ring_candidate", return_value=_candidate_ring())
    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_candidate_ring_can_score_and_preserves_lineage(self, lookup, _evaluate):
        lookup.return_value = _snapshot({
            1: {"findings": [_finding("Ring", 99, finding_hash="historical")]},
        })
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertEqual(result["fraud_score"], 82)
        self.assertEqual(result["winning_finding"]["evidence_scope"], "CURRENT_NOMINATION")
        self.assertEqual(result["winning_finding"]["path_user_ids"], [2, 4, 1, 2])
        self.assertFalse(next(
            item for item in result["pattern_findings"]
            if item["finding_hash"] == "historical"
        )["routing_relevant"])

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
        self.assertEqual(result["fraud_score"], 64.25)
        self.assertEqual(result["risk_level"], "MEDIUM")
        self.assertEqual(result["winning_finding_hash"], "super")
        by_hash = {item["finding_hash"]: item for item in result["pattern_findings"]}
        self.assertFalse(by_hash["wrong-role"]["routing_relevant"])
        self.assertFalse(by_hash["analytics"]["routing_relevant"])
        self.assertFalse(by_hash["ring"]["routing_relevant"])

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
        lookup.return_value = _snapshot()

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
        self.assertEqual(completed.fraud_score, 0)
        self.assertEqual(completed.risk_level, "NONE")
        self.assertIsNone(completed.winning_pattern_type)
        self.assertEqual(completed.snapshot_run_id, "graph-run-1")
        self.assertEqual(completed.scoring_policy_version, 2)
        self.assertEqual(completed.finding_count, 0)

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
            'snapshot_schema_version': 2, 'scoring_policy_version': 2, 'finding_count': 3,
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


class GraphInferenceArtifactTests(unittest.TestCase):
    def setUp(self):
        os.environ["AZURE_STORAGE_ACCOUNT"] = "teststorage"
        graph_check._snapshot_cache.clear()

    def _artifact(self):
        raw = json.dumps(_inference_snapshot().to_dict()).encode("utf-8")
        compressed = gzip.compress(raw, mtime=0)
        metadata = {
            "snapshot_run_id": "graph-run-1",
            "scoring_policy_version": 2,
            "inference_snapshot_blob": "graph/tenant-7/graph-run-1/inference-snapshot.json.gz",
            "inference_snapshot_sha256": hashlib.sha256(compressed).hexdigest(),
            "inference_snapshot_size_bytes": len(compressed),
        }
        return compressed, metadata

    @patch("azure.storage.blob.BlobServiceClient")
    def test_verified_artifact_is_cached_by_run_and_checksum(self, blob_service):
        compressed, metadata = self._artifact()
        blob_service.return_value.get_blob_client.return_value.download_blob.return_value.readall.return_value = compressed

        first = graph_check._load_inference_snapshot(7, metadata)
        second = graph_check._load_inference_snapshot(7, metadata)

        self.assertIs(first, second)
        self.assertEqual(first.run_id, "graph-run-1")
        self.assertEqual(blob_service.return_value.get_blob_client.call_count, 1)

    @patch("azure.storage.blob.BlobServiceClient")
    def test_checksum_mismatch_is_invalid_snapshot(self, blob_service):
        compressed, metadata = self._artifact()
        metadata["inference_snapshot_sha256"] = "0" * 64
        blob_service.return_value.get_blob_client.return_value.download_blob.return_value.readall.return_value = compressed
        with self.assertRaisesRegex(db.InvalidGraphSnapshot, "checksum mismatch"):
            graph_check._load_inference_snapshot(7, metadata)


if __name__ == "__main__":
    unittest.main()
