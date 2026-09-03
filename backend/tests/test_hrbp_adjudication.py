"""Tests for model-neutral HRBP adjudication routing.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\backend"
    python -m unittest tests.test_hrbp_adjudication -v
"""

import json
import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


os.environ.setdefault("CLIENT_ID", "unit-test-client")

from fastapi import HTTPException
from routers.hrbp_router import HRBPDecisionRequest, hrbp_decide
from utils import sqlhelper2


_USER_CONTEXT = {
    "effective_user": {"UserId": 77, "TenantId": 3},
}

_DETAILS = {
    "nomination_id": 13880,
    "tenant_id": 3,
    "status": "PendingHRBPReview",
}


class HRBPAdjudicationTests(unittest.IsolatedAsyncioTestCase):
    @patch("routers.hrbp_router.publish_event", new_callable=AsyncMock)
    @patch("routers.hrbp_router.sqlhelper.apply_hrbp_adjudication")
    @patch("routers.hrbp_router.sqlhelper.get_nomination_details_for_hrbp")
    async def test_unsubstantiated_clear_is_explicitly_excluded_from_training(
        self, get_details, apply_adjudication, publish_event
    ):
        get_details.return_value = _DETAILS
        apply_adjudication.return_value = {
            "applied": True,
            "status": "Pending",
            "outcome": "CLEARED_UNSUBSTANTIATED",
            "training_disposition": "EXCLUDED",
        }

        result = await hrbp_decide(
            nomination_id=13880,
            body=HRBPDecisionRequest(
                outcome="CLEARED_UNSUBSTANTIATED",
                reason="Available evidence did not substantiate the concern.",
            ),
            user_context=_USER_CONTEXT,
        )

        apply_adjudication.assert_called_once_with(
            nomination_id=13880,
            outcome="CLEARED_UNSUBSTANTIATED",
            reviewed_by="HRBP:77",
            reason="Available evidence did not substantiate the concern.",
        )
        self.assertEqual(result["training_disposition"], "EXCLUDED")
        self.assertEqual(
            [call.args[0] for call in publish_event.await_args_list],
            ["nomination.hrbp-approved", "nomination.created"],
        )

    @patch("routers.hrbp_router.publish_event", new_callable=AsyncMock)
    @patch("routers.hrbp_router.sqlhelper.apply_hrbp_adjudication")
    @patch("routers.hrbp_router.sqlhelper.get_nomination_details_for_hrbp")
    async def test_confirmed_concern_creates_fraud_disposition_and_rejection_event(
        self, get_details, apply_adjudication, publish_event
    ):
        get_details.return_value = _DETAILS
        apply_adjudication.return_value = {
            "applied": True,
            "status": "Rejected",
            "outcome": "CONFIRMED_CONCERN",
            "training_disposition": "FRAUD",
        }

        result = await hrbp_decide(
            nomination_id=13880,
            body=HRBPDecisionRequest(
                outcome="CONFIRMED_CONCERN",
                reason="The reciprocal activity was confirmed.",
            ),
            user_context=_USER_CONTEXT,
        )

        self.assertEqual(result["status"], "Rejected")
        self.assertEqual(result["training_disposition"], "FRAUD")
        publish_event.assert_awaited_once()
        self.assertEqual(
            publish_event.await_args.args[0], "nomination.hrbp-rejected"
        )

    @patch("routers.hrbp_router.sqlhelper.get_nomination_details_for_hrbp")
    async def test_every_human_decision_requires_a_reason(self, get_details):
        get_details.return_value = _DETAILS

        with self.assertRaises(HTTPException) as raised:
            await hrbp_decide(
                nomination_id=13880,
                body=HRBPDecisionRequest(
                    outcome="CLEARED_NO_CONCERN",
                    reason="   ",
                ),
                user_context=_USER_CONTEXT,
            )

        self.assertEqual(raised.exception.status_code, 400)


class HRBPAdjudicationPersistenceTests(unittest.TestCase):
    def test_excluded_outcome_updates_decision_result_without_touching_rf_score(self):
        session = MagicMock()
        current = MagicMock()
        current.fetchone.return_value = ("FRAUD", None)
        session.execute.side_effect = [
            current,
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=1),
        ]

        @contextmanager
        def fake_db_context():
            yield session

        with patch.object(sqlhelper2, "get_db_context", fake_db_context):
            result = sqlhelper2.apply_hrbp_adjudication(
                nomination_id=13880,
                outcome="CLEARED_UNSUBSTANTIATED",
                reviewed_by="HRBP:77",
                reason="Concern not substantiated.",
            )

        integrity_sql = str(session.execute.call_args_list[1].args[0])
        decision_params = session.execute.call_args_list[1].args[1]
        all_sql = "\n".join(str(call.args[0]) for call in session.execute.call_args_list)

        self.assertTrue(result["applied"])
        self.assertEqual(result["training_disposition"], "EXCLUDED")
        self.assertEqual(decision_params["training_disposition"], "EXCLUDED")
        self.assertIn("IntegrityDecisionResults", integrity_sql)
        self.assertNotIn("FraudDecisionResults", all_sql)
        self.assertNotIn("RfScore", integrity_sql)
        session.commit.assert_called_once()

    def test_semantic_only_review_cannot_create_a_fraud_training_label(self):
        session = MagicMock()
        current = MagicMock()
        current.fetchone.return_value = ("SEMANTIC", None)
        session.execute.return_value = current

        @contextmanager
        def fake_db_context():
            yield session

        with patch.object(sqlhelper2, "get_db_context", fake_db_context):
            with self.assertRaisesRegex(ValueError, "not valid for review scope"):
                sqlhelper2.apply_hrbp_adjudication(
                    nomination_id=13880,
                    outcome="CONFIRMED_CONCERN",
                    reviewed_by="HRBP:77",
                    reason="Concern confirmed.",
                )

        self.assertEqual(session.execute.call_count, 1)
        session.commit.assert_not_called()

    def test_confirmed_semantic_concern_is_rejected_and_excluded(self):
        session = MagicMock()
        current = MagicMock()
        current.fetchone.return_value = ("SEMANTIC", None)
        session.execute.side_effect = [
            current,
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=1),
        ]

        @contextmanager
        def fake_db_context():
            yield session

        with patch.object(sqlhelper2, "get_db_context", fake_db_context):
            result = sqlhelper2.apply_hrbp_adjudication(
                nomination_id=13880,
                outcome="CONFIRMED_SEMANTIC_CONCERN",
                reviewed_by="HRBP:77",
                reason="The description concern was confirmed.",
            )

        self.assertEqual(result["status"], "Rejected")
        self.assertEqual(result["training_disposition"], "EXCLUDED")
        self.assertEqual(result["review_scope"], "SEMANTIC")


class HRBPQueueProjectionTests(unittest.TestCase):
    def test_new_decision_exposes_all_four_engine_documents(self):
        rf = {
            "engine": "RF",
            "available": True,
            "score": 48,
            "risk_level": "MEDIUM",
            "findings": ["[RF] Reciprocal nomination detected"],
            "explanation": {"llm_text": "RF evidence warrants review."},
        }
        graph = {
            "engine": "GRAPH",
            "available": True,
            "score": 50,
            "risk_level": "MEDIUM",
            "findings": ["[Graph] Beneficiary is an outlier"],
        }
        gnn = {
            "engine": "GNN",
            "available": False,
            "risk_level": "UNKNOWN",
            "findings": [],
        }
        semantic = {
            "engine": "SEMANTIC",
            "available": True,
            "combined_decision": {"action": "pass", "checks": []},
        }
        row = (
            13880, "PendingHRBPReview", 5000, "USD", "Description",
            "2026-08-28", "Nominator", "nom@example.com", "Beneficiary",
            "ben@example.com", 50, "MEDIUM",
            2, "FRAUD", '["GRAPH"]', json.dumps(rf), json.dumps(graph),
            json.dumps(gnn), json.dumps(semantic), "HRBP_REVIEW",
            "fraud_concern_hrbp",
        )
        session = MagicMock()
        query = MagicMock()
        query.fetchall.return_value = [row]
        session.execute.return_value = query

        @contextmanager
        def fake_db_context():
            yield session

        with patch.object(sqlhelper2, "get_db_context", fake_db_context):
            item = sqlhelper2.get_hrbp_queue(3)[0]

        self.assertEqual(item["decision_source"], "integrity_v2")
        self.assertEqual(item["review_scope"], "FRAUD")
        self.assertEqual(item["decisive_engines"], ["GRAPH"])
        self.assertEqual(item["engine_results"]["rf"]["score"], 48)
        self.assertEqual(item["engine_results"]["semantic"]["engine"], "SEMANTIC")
        self.assertEqual(item["llm_explanation"], "RF evidence warrants review.")
        self.assertIn("[Graph] Beneficiary is an outlier", item["warning_flags"])


if __name__ == "__main__":
    unittest.main()
