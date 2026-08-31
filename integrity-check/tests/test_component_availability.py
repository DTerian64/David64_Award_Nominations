"""Tests for producer-status enrichment of live component availability.

Purpose:
    Verify that durable training/analytics reasons are exposed at inference and
    that registry/artifact drift is never mistaken for an eligibility skip.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m unittest discover -s tests -p "test_component_availability.py" -v
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import component_availability


class ComponentAvailabilityTests(unittest.TestCase):
    def test_training_skip_reason_replaces_generic_no_model(self):
        attempted_at = datetime(2026, 8, 24, 2, 4, 12, tzinfo=timezone.utc)
        status = {
            "serving_status": "UNAVAILABLE",
            "last_attempt_status": "SKIPPED",
            "reason_code": "BELOW_MINIMUM_VOLUME",
            "reason_detail": "75 nominations / 108 users; requires 300 / 50",
            "last_attempt_at": attempted_at,
            "run_id": "run-1",
        }

        result = component_availability.unavailable_metadata(
            "GNN", "NO_MODEL", status, source_missing=True
        )

        self.assertEqual(result["unavailable_reason"], "BELOW_MINIMUM_VOLUME")
        self.assertEqual(
            result["unavailable_detail"],
            "75 nominations / 108 users; requires 300 / 50",
        )
        self.assertEqual(result["last_attempt_status"], "SKIPPED")
        self.assertEqual(result["last_attempt_at"], attempted_at)

    def test_registry_artifact_mismatch_is_operational_failure(self):
        status = {
            "serving_status": "AVAILABLE",
            "last_attempt_status": "SUCCEEDED",
            "reason_code": None,
            "serving_version": "gnn-20260817-t3",
        }

        result = component_availability.unavailable_metadata(
            "GNN", "NO_MODEL", status, source_missing=True
        )

        self.assertEqual(result["unavailable_reason"], "ARTIFACT_MISSING")
        self.assertEqual(result["last_serving_version"], "gnn-20260817-t3")

    def test_runtime_nomination_failure_is_not_replaced_by_training_state(self):
        status = {
            "serving_status": "AVAILABLE",
            "last_attempt_status": "SUCCEEDED",
            "reason_code": None,
        }

        result = component_availability.unavailable_metadata(
            "GNN", "COLD_START_USER", status, source_missing=False
        )

        self.assertEqual(result["unavailable_reason"], "COLD_START_USER")


if __name__ == "__main__":
    unittest.main()
