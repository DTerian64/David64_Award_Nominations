"""Tests for atomic legacy/new integrity decision persistence.

Purpose:
    Guard the SQL parameter contract that persists each component's availability
    reason beside the nomination-level fused decision.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m unittest discover -s tests -p "test_decision_persistence.py" -v
"""

import os
import sys
import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import db


class _Cursor:
    def __init__(self, reconciliation_row=None):
        self.calls = []
        self.reconciliation_row = reconciliation_row or (
            1, 7, "NONE",
            1, 0, "NONE",
            0, 0, "NONE",
            7, "NONE",
            json.dumps({"available": True, "score": 7, "risk_level": "NONE"}),
            json.dumps({"available": True, "score": 0, "risk_level": "NONE"}),
            json.dumps({"available": False, "score": None, "risk_level": "UNKNOWN"}),
            7, "NONE",
        )

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self.reconciliation_row


class _Connection:
    def __init__(self, reconciliation_row=None):
        self.cursor_value = _Cursor(reconciliation_row)
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
            "decision_available": True,
            "final_score": 7,
            "risk_level": "NONE",
            "decisive_models": ["RF", "Graph"],
        }
        engine_results = {
            "rf": {"available": True, "score": 7, "risk_level": "NONE"},
            "graph": {"available": True, "score": 0, "risk_level": "NONE"},
            "gnn": {"available": False, "score": None, "risk_level": "UNKNOWN"},
            "semantic": {"engine": "SEMANTIC", "status": "SUCCEEDED"},
        }

        with patch("utils.db._get_conn", return_value=_connection_context(conn)):
            db.save_integrity_decision_results(
                nomination_id=13866,
                message_id="message-13866",
                policy_version="max-severity-v1",
                rf_result=rf,
                graph_result=graph,
                gnn_result=gnn,
                decision=decision,
                engine_results=engine_results,
                final_route="MANAGER_APPROVAL",
                routing_rule="risk_below_review_threshold",
                review_scope=None,
                decisive_engines=["RF", "GRAPH"],
            )

        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.cursor_value.calls), 3)
        for sql, params in conn.cursor_value.calls:
            self.assertEqual(sql.count("?"), len(params))
        legacy_sql, legacy_params = conn.cursor_value.calls[0]
        new_sql, new_params = conn.cursor_value.calls[1]
        reconciliation_sql, _ = conn.cursor_value.calls[2]
        self.assertIn("GnnUnavailableReasonCode", legacy_sql)
        self.assertIn("IntegrityDecisionResults", new_sql)
        self.assertIn("BELOW_MINIMUM_VOLUME", legacy_params)
        self.assertIn(
            "75 nominations / 108 users; requires 300 / 50",
            legacy_params,
        )
        self.assertIn("message-13866", new_params)
        self.assertIn('["RF","GRAPH"]', new_params)
        self.assertNotIn(" AS current", reconciliation_sql)
        self.assertIn(" AS idr", reconciliation_sql)

    def test_no_available_decision_reconciles_legacy_zero_with_new_null(self):
        unavailable_document = json.dumps({
            "available": False,
            "score": None,
            "risk_level": "UNKNOWN",
        })
        conn = _Connection((
            0, 0, "NONE", 0, 0, "NONE", 0, 0, "NONE",
            0, "UNKNOWN",
            unavailable_document, unavailable_document, unavailable_document,
            None, "UNKNOWN",
        ))
        unavailable = {
            "model_available": False,
            "fraud_score": 0,
            "risk_level": "NONE",
        }
        engine_results = {
            "rf": json.loads(unavailable_document),
            "graph": json.loads(unavailable_document),
            "gnn": json.loads(unavailable_document),
            "semantic": {"engine": "SEMANTIC", "status": "SUCCEEDED"},
        }

        with patch("utils.db._get_conn", return_value=_connection_context(conn)):
            db.save_integrity_decision_results(
                nomination_id=13867,
                message_id="message-13867",
                policy_version="max-severity-v1",
                rf_result=unavailable,
                graph_result=unavailable,
                gnn_result=unavailable,
                decision={
                    "decision_available": False,
                    "final_score": 0,
                    "risk_level": "UNKNOWN",
                    "decisive_models": [],
                },
                engine_results=engine_results,
                final_route="MANAGER_APPROVAL",
                routing_rule="no_available_fraud_opinion",
                review_scope=None,
                decisive_engines=[],
            )

        self.assertTrue(conn.committed)


if __name__ == "__main__":
    unittest.main()
