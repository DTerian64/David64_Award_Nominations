"""Tests for the FraudDecisionResults component-availability snapshot.

Purpose:
    Guard the SQL parameter contract that persists each component's availability
    reason beside the nomination-level fused decision.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m unittest discover -s tests -p "test_decision_persistence.py" -v
"""

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import db


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

    def close(self):
        pass


@contextmanager
def _connection_context(conn):
    yield conn


class DecisionPersistenceTests(unittest.TestCase):
    def test_unavailable_reason_codes_and_details_are_persisted(self):
        conn = _Connection()
        rf = {"model_available": True, "fraud_score": 7, "risk_level": "NONE"}
        graph = {"model_available": True, "fraud_score": 0, "risk_level": "NONE"}
        gnn = {
            "model_available": False,
            "fraud_score": 0,
            "risk_level": "NONE",
            "unavailable_reason": "BELOW_MINIMUM_VOLUME",
            "unavailable_detail": "75 nominations / 108 users; requires 300 / 50",
        }
        decision = {
            "final_score": 7,
            "risk_level": "NONE",
            "decisive_models": ["RF", "Graph"],
        }

        with patch("utils.db._get_conn", return_value=_connection_context(conn)):
            db.save_fraud_decision_result(
                nomination_id=13866,
                policy_version="max-severity-v1",
                rf_result=rf,
                graph_result=graph,
                gnn_result=gnn,
                decision=decision,
            )

        self.assertTrue(conn.committed)
        self.assertEqual(conn.cursor_value.sql.count("?"), len(conn.cursor_value.params))
        self.assertIn("GnnUnavailableReasonCode", conn.cursor_value.sql)
        self.assertIn("BELOW_MINIMUM_VOLUME", conn.cursor_value.params)
        self.assertIn(
            "75 nominations / 108 users; requires 300 / 50",
            conn.cursor_value.params,
        )


if __name__ == "__main__":
    unittest.main()
