"""Tests for durable fraud-component producer status.

Purpose:
    Ensure a structured reason, diagnostics, and serving/attempt state are sent
    atomically to dbo.IntegrityComponentStatus.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\fraud-analytics-job"
    python -m unittest tests.test_component_status -v
"""

import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.component_status import upsert_component_status


class _Cursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params


class _Connection:
    def __init__(self):
        self.cursor_value = _Cursor()
        self.committed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True


class ComponentStatusTests(unittest.TestCase):
    def test_structured_skip_is_upserted_and_committed(self):
        conn = _Connection()

        upsert_component_status(
            conn,
            tenant_id=3,
            component="GNN",
            attempt_status="SKIPPED",
            reason_code="BELOW_MINIMUM_VOLUME",
            reason_detail="75 nominations / 108 users; requires 300 / 50",
            diagnostics={
                "nomination_count": 75,
                "user_count": 108,
                "minimum_nominations": 300,
                "minimum_users": 50,
            },
            run_id="run-1",
        )

        self.assertTrue(conn.committed)
        self.assertIn("MERGE dbo.IntegrityComponentStatus", conn.cursor_value.sql)
        self.assertEqual(conn.cursor_value.sql.count("?"), len(conn.cursor_value.params))
        self.assertIn("BELOW_MINIMUM_VOLUME", conn.cursor_value.params)
        diagnostic_values = [
            value for value in conn.cursor_value.params
            if isinstance(value, str) and value.startswith("{")
        ]
        self.assertEqual(json.loads(diagnostic_values[0])["nomination_count"], 75)


if __name__ == "__main__":
    unittest.main()
