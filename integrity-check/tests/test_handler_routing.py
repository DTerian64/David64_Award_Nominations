"""Tests for complete-assessment-before-routing behavior in the integrity handler.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m unittest discover -s tests -p "test_handler_routing.py" -v

All database, Service Bus, and model calls are mocked.
"""

import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
os.environ.setdefault("AZURE_STORAGE_ACCOUNT", "teststorage")
os.environ.setdefault("SERVICE_BUS_FQNS", "test.servicebus.invalid")
os.environ.setdefault("SERVICE_BUS_TOPIC_NAME", "test-topic")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import description_check
import handler


def component_unavailable(reason: str) -> dict:
    return {
        "model_available": False,
        "unavailable_reason": reason,
        "fraud_score": 0,
        "fraud_prob": None,
        "risk_level": "NONE",
        "warning_flags": [],
    }


def decision(risk: str = "NONE", *, available: bool = True) -> dict:
    flagged = risk in ("MEDIUM", "HIGH", "CRITICAL")
    return {
        "decision_available": available,
        "risk_level": risk if available else "UNKNOWN",
        "flagged": flagged,
    }


class RoutingRuleTests(unittest.TestCase):
    def test_description_rejection_has_final_routing_priority(self):
        desc = description_check.CheckResult("reject", "Incoherent.", "category_alignment")
        route = handler._select_route(desc, decision("CRITICAL"))
        self.assertEqual(route["route"], "REJECT_DESCRIPTION")

    def test_description_concern_routes_to_hrbp(self):
        desc = description_check.CheckResult("flag", "Category concern.", "category_alignment")
        route = handler._select_route(desc, decision("NONE"))
        self.assertEqual(route["target_status"], "PendingHRBPReview")

    def test_model_concern_routes_to_hrbp(self):
        desc = description_check.CheckResult("pass", None, None)
        route = handler._select_route(desc, decision("HIGH"))
        self.assertEqual(route["target_status"], "PendingHRBPReview")

    def test_critical_model_risk_rejects(self):
        desc = description_check.CheckResult("pass", None, None)
        route = handler._select_route(desc, decision("CRITICAL"))
        self.assertEqual(route["route"], "REJECT_FRAUD")

    def test_clean_available_decision_routes_to_manager(self):
        desc = description_check.CheckResult("pass", None, None)
        route = handler._select_route(desc, decision("NONE"))
        self.assertEqual(route["target_status"], "Pending")


class CompleteAssessmentTests(unittest.TestCase):
    def test_description_rejection_still_runs_all_components_and_persists_decision(self):
        call_order: list[str] = []
        details = {
            "tenant_id": 3,
            "nominator_id": 435,
            "description": "%%%% incoherent %%%%",
            "category_description": "Going Above & Beyond",
            "amount": 3000,
        }
        desc_reject = description_check.CheckResult(
            action="reject",
            reason="Nomination description appears incoherent.",
            check="category_alignment",
        )

        with ExitStack() as stack:
            stack.enter_context(patch("handler.db.claim_message", return_value=False))
            stack.enter_context(patch("handler.db.get_nomination_details", return_value=details))
            stack.enter_context(patch("handler.db.get_tenant_desc_check_config", return_value=object()))
            stack.enter_context(patch("handler.description_check.check", return_value=desc_reject))

            rf = stack.enter_context(patch("handler.fraud_check.assess"))
            graph = stack.enter_context(patch("handler.graph_check.assess_graph"))
            gnn = stack.enter_context(patch("handler.gnn_check.assess_gnn"))
            rf.side_effect = lambda *_args: (
                call_order.append("rf") or component_unavailable("test_rf")
            )
            graph.side_effect = lambda *_args: (
                call_order.append("graph") or component_unavailable("test_graph")
            )
            gnn.side_effect = lambda *_args: (
                call_order.append("gnn") or component_unavailable("test_gnn")
            )

            save_decision = stack.enter_context(
                patch("handler.db.save_fraud_decision_result")
            )
            save_decision.side_effect = lambda **_kwargs: call_order.append("decision")
            reject = stack.enter_context(patch("handler.db.reject_nomination"))
            reject.side_effect = lambda *_args, **_kwargs: call_order.append("reject")
            publish = stack.enter_context(
                patch("handler.service_bus_publisher.publish_event")
            )
            update_event = stack.enter_context(
                patch("handler.db.update_processed_event_result")
            )

            with self.assertLogs("integrity_check.handler", level="INFO") as logs:
                handler.handle("message-1", {
                    "event_type": "nomination.submitted",
                    "nomination_id": 13866,
                })

        self.assertEqual(call_order, ["rf", "graph", "gnn", "decision", "reject"])
        save_decision.assert_called_once()
        reject.assert_called_once()
        update_event.assert_called_once_with("message-1", "success")
        self.assertEqual(publish.call_args_list[-1].args[0], "nomination.description-rejected")

        messages = [record.getMessage() for record in logs.records]
        for expected in (
            "RF assessment starting",
            "RF assessment completed",
            "Graph Analytics assessment starting",
            "Graph Analytics assessment completed",
            "GNN assessment starting",
            "GNN assessment completed",
            "FraudDecisionResults persisted",
            "Rules-based routing decision",
        ):
            self.assertIn(expected, messages)


if __name__ == "__main__":
    unittest.main()
