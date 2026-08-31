import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import graph_check


DETAILS = {
    "nomination_id": 10,
    "nominator_id": 1,
    "beneficiary_id": 2,
    "approver_id": 3,
}


class GraphCheckTests(unittest.TestCase):
    @patch("inference.graph_check.db.get_tenant_integrity_config", return_value={})
    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_highest_participant_severity_is_independent_graph_score(self, lookup, _config):
        lookup.return_value = {
            "snapshot_as_of": date.today(),
            "users": {
                1: {
                    "highest_severity": "Medium",
                    "is_in_ring": True,
                    "is_super_nominator": False,
                    "is_in_copy_paste_cluster": False,
                    "has_transactional_language": False,
                    "is_approver_affinity": False,
                },
                3: {
                    "highest_severity": "Critical",
                    "is_in_ring": False,
                    "is_super_nominator": False,
                    "is_in_copy_paste_cluster": False,
                    "has_transactional_language": False,
                    "is_approver_affinity": True,
                },
            },
        }
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertTrue(result["model_available"])
        self.assertEqual(result["risk_level"], "CRITICAL")
        self.assertEqual(result["fraud_score"], 100)
        self.assertEqual(result["affected_user_ids"], [1, 3])

    @patch("inference.graph_check.db.get_graph_component_snapshot", return_value=None)
    def test_missing_snapshot_is_no_opinion_not_clean(self, _lookup):
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertFalse(result["model_available"])
        self.assertEqual(result["unavailable_reason"], "NO_SNAPSHOT")

    @patch("inference.graph_check.db.get_tenant_integrity_config", return_value={})
    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_missing_participant_row_on_current_snapshot_is_clean(self, lookup, _config):
        lookup.return_value = {"snapshot_as_of": date.today(), "users": {}}
        result = graph_check.assess_graph(DETAILS, tenant_id=7)
        self.assertTrue(result["model_available"])
        self.assertEqual(result["risk_level"], "NONE")

    @patch("inference.graph_check.db.get_tenant_integrity_config")
    @patch("inference.graph_check.db.get_graph_component_snapshot")
    def test_tenant_thresholds_control_graph_routing(self, lookup, config):
        lookup.return_value = {
            "snapshot_as_of": date.today(),
            "users": {
                1: {
                    "highest_severity": "High",
                    "is_in_ring": True,
                    "is_super_nominator": False,
                    "is_in_copy_paste_cluster": False,
                    "has_transactional_language": False,
                    "is_approver_affinity": False,
                },
            },
        }
        config.return_value = {
            "graph": {
                "score_routing": {
                    "low_threshold": 20,
                    "medium_threshold": 40,
                    "high_threshold": 80,
                    "critical_threshold": 95,
                }
            }
        }

        result = graph_check.assess_graph(DETAILS, tenant_id=7)

        self.assertEqual(result["source_severity"], "HIGH")
        self.assertEqual(result["fraud_score"], 75)
        self.assertEqual(result["risk_level"], "MEDIUM")


if __name__ == "__main__":
    unittest.main()
