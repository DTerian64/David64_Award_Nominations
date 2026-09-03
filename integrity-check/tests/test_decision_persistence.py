"""Tests for canonical integrity decision persistence.

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
    def __init__(self, rowcount=1):
        self.calls = []
        self.rowcount = rowcount

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return self

class _Connection:
    def __init__(self, rowcount=1):
        self.cursor_value = _Cursor(rowcount)
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
                decision=decision,
                engine_results=engine_results,
                final_route="MANAGER_APPROVAL",
                routing_rule="risk_below_review_threshold",
                review_scope=None,
                decisive_engines=["RF", "GRAPH"],
            )

        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.cursor_value.calls), 1)
        for sql, params in conn.cursor_value.calls:
            self.assertEqual(sql.count("?"), len(params))
        new_sql, new_params = conn.cursor_value.calls[0]
        self.assertIn("IntegrityDecisionResults", new_sql)
        self.assertNotIn("FraudDecisionResults", new_sql)
        self.assertIn("message-13866", new_params)
        self.assertIn('["RF","GRAPH"]', new_params)
        self.assertEqual(new_params[:2], ("message-13866", 13866))
        self.assertIn("JOIN dbo.Users u ON u.UserId = n.NominatorId", new_sql)
        self.assertIn("target.TenantId = source.TenantId", new_sql)
        self.assertIn("TenantId = source.TenantId,", new_sql)
        self.assertIn("TenantId, NominationId, DecisionSchemaVersion", new_sql)
        self.assertIn("VALUES (source.TenantId,", new_sql)

    def test_unresolved_tenant_or_conflicting_decision_does_not_commit(self):
        conn = _Connection(rowcount=0)
        with patch("utils.db._get_conn", return_value=_connection_context(conn)):
            with self.assertRaisesRegex(RuntimeError, "tenant.*source message"):
                db.save_integrity_decision_results(
                    nomination_id=13866,
                    message_id="message-13866",
                    policy_version="max-severity-v1",
                    decision={"decision_available": False, "risk_level": "UNKNOWN"},
                    engine_results={name: {} for name in ("rf", "graph", "gnn", "semantic")},
                    final_route="MANAGER_APPROVAL",
                    routing_rule="no_available_fraud_opinion",
                    review_scope=None,
                    decisive_engines=[],
                )
        self.assertFalse(conn.committed)

    def test_no_available_decision_persists_null_composite_score(self):
        unavailable_document = json.dumps({
            "available": False,
            "score": None,
            "risk_level": "UNKNOWN",
        })
        conn = _Connection()
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
        _, params = conn.cursor_value.calls[0]
        self.assertIn(None, params)


if __name__ == "__main__":
    unittest.main()
