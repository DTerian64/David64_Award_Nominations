"""Tests for Setup > Detection Engines read-only status.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\backend"
    python -m unittest discover -s tests -p "test_detection_engines.py" -v
"""

import os
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch


os.environ.setdefault("CLIENT_ID", "unit-test-client")

from routers.setup_router import get_detection_engines
from utils import sqlhelper2


class DetectionEnginesEndpointTests(unittest.IsolatedAsyncioTestCase):
    @patch("routers.setup_router.sqlhelper.get_integrity_component_statuses")
    async def test_endpoint_is_scoped_to_authenticated_admin_tenant(self, get_statuses):
        get_statuses.return_value = [{"component": "RF", "serving_status": "AVAILABLE"}]

        result = await get_detection_engines(admin={"TenantId": 3})

        get_statuses.assert_called_once_with(3)
        self.assertEqual(result["rows"][0]["component"], "RF")


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.params = None
        self.statement = None

    def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return _Result(self.rows)


@contextmanager
def _session_context(session):
    yield session


class DetectionEnginesSqlTests(unittest.TestCase):
    def test_status_row_includes_structured_diagnostics_and_utc_times(self):
        session = _Session([(
            "GNN", "UNAVAILABLE", None, None, "SKIPPED",
            "BELOW_MINIMUM_VOLUME",
            "75 nominations / 108 users; requires 300 / 50",
            '{"nomination_count":75,"minimum_nominations":300}',
            datetime(2026, 8, 24, 2, 4, 12), None, "run-1",
            datetime(2026, 8, 24, 2, 4, 13), "svc:fraud-analytics-job",
        )])

        with patch(
            "utils.sqlhelper2.get_db_context",
            return_value=_session_context(session),
        ):
            rows = sqlhelper2.get_integrity_component_statuses(tenant_id=3)

        self.assertEqual(session.params, {"tid": 3})
        self.assertEqual(rows[0]["reason_code"], "BELOW_MINIMUM_VOLUME")
        self.assertEqual(rows[0]["diagnostics"]["nomination_count"], 75)
        self.assertEqual(rows[0]["last_attempt_at"], "2026-08-24T02:04:12Z")

    def test_gnn_run_history_uses_temporal_rows_and_latest_candidate_selection(self):
        diagnostics = (
            '{"artifact_bundle_prefix":"gnn/tenant_5/gnn-v2-run-1",'
            '"selection":{"selected_architecture":"graphsage"},'
            '"last_candidate_selection":{"model_version":"gnn-v2-run-1",'
            '"selected_architecture":null,'
            '"selection_reason":"NO_GRAPH_VALUE_OVER_MLP"}}'
        )
        session = _Session([(
            "AVAILABLE", "gnn-v2-serving", datetime(2026, 9, 1),
            "SKIPPED", "NO_GRAPH_VALUE_OVER_MLP", "No graph winner",
            diagnostics, datetime(2026, 9, 13, 20, 13),
            datetime(2026, 9, 1), "run-1", datetime(2026, 9, 13, 20, 14),
            "svc:fraud-analytics-job", datetime(2026, 9, 13, 20, 13),
            datetime(9999, 12, 31, 23, 59, 59),
        )])

        with patch(
            "utils.sqlhelper2.get_db_context",
            return_value=_session_context(session),
        ):
            rows = sqlhelper2.get_gnn_training_runs(tenant_id=5, limit=12)

        self.assertIn("FOR SYSTEM_TIME ALL", session.statement)
        self.assertIn("PARTITION BY RunId", session.statement)
        self.assertEqual(session.params, {"tid": 5, "limit": 12})
        self.assertEqual(rows[0]["model_version"], "gnn-v2-run-1")
        self.assertIsNone(rows[0]["selected_architecture"])
        self.assertEqual(rows[0]["selection_reason"], "NO_GRAPH_VALUE_OVER_MLP")
        self.assertEqual(
            rows[0]["artifact_bundle_prefix"],
            "gnn/tenant_5/gnn-v2-run-1",
        )
        self.assertTrue(rows[0]["is_current"])


if __name__ == "__main__":
    unittest.main()
