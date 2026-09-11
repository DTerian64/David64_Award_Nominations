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

from inference import description_check
from inference import handler


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
    @patch.object(handler.graph_check, "_evict_idle_snapshots", return_value=3)
    @patch.object(handler.gnn_check, "_evict_idle_heads", return_value=2)
    @patch.object(handler.random_forest_check, "_evict_idle_models", return_value=1)
    def test_idle_artifact_eviction_covers_all_three_engines(
        self, evict_rf, evict_gnn, evict_graph
    ):
        self.assertEqual(
            handler.evict_idle_artifacts(),
            {"rf": 1, "gnn": 2, "graph": 3},
        )
        evict_rf.assert_called_once_with()
        evict_gnn.assert_called_once_with()
        evict_graph.assert_called_once_with()

    def test_description_rejection_has_final_routing_priority(self):
        desc = description_check.CheckResult("reject", "Incoherent.", "category_alignment")
        route = handler._select_route(desc, decision("CRITICAL"))
        self.assertEqual(route["route"], "REJECT_SEMANTIC")
        self.assertIsNone(route["review_scope"])

    def test_description_concern_routes_to_hrbp(self):
        desc = description_check.CheckResult("flag", "Category concern.", "category_alignment")
        route = handler._select_route(desc, decision("NONE"))
        self.assertEqual(route["target_status"], "PendingHRBPReview")
        self.assertEqual(route["review_scope"], "SEMANTIC")

    def test_model_concern_routes_to_hrbp(self):
        desc = description_check.CheckResult("pass", None, None)
        route = handler._select_route(desc, decision("HIGH"))
        self.assertEqual(route["target_status"], "PendingHRBPReview")
        self.assertEqual(route["review_scope"], "FRAUD")

    def test_combined_concern_has_combined_review_scope(self):
        desc = description_check.CheckResult("flag", "Category concern.", None)
        route = handler._select_route(desc, decision("HIGH"))
        self.assertEqual(route["review_scope"], "FRAUD_AND_SEMANTIC")

    def test_semantic_only_route_names_only_semantic_as_decisive(self):
        desc = description_check.CheckResult("flag", "Category concern.", None)
        fused = {
            **decision("NONE"),
            "decisive_models": ["RF", "Graph"],
        }
        route = handler._select_route(desc, fused)
        self.assertEqual(
            handler._decisive_engines(desc, fused, route),
            ["SEMANTIC"],
        )

    def test_critical_model_risk_routes_to_priority_hrbp_review(self):
        desc = description_check.CheckResult("pass", None, None)
        route = handler._select_route(desc, decision("CRITICAL"))
        self.assertEqual(route["route"], "HRBP_REVIEW")
        self.assertEqual(route["target_status"], "PendingHRBPReview")
        self.assertEqual(route["review_priority"], "CRITICAL")

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
            stack.enter_context(patch("inference.handler.db.claim_message", return_value=False))
            stack.enter_context(patch("inference.handler.db.get_nomination_details", return_value=details))
            stack.enter_context(patch("inference.handler.db.get_tenant_desc_check_config", return_value=object()))
            stack.enter_context(patch("inference.handler.db.get_integrity_component_statuses", return_value={}))
            stack.enter_context(patch("inference.handler.description_check.check", return_value=desc_reject))

            rf = stack.enter_context(patch("inference.handler.random_forest_check.assess"))
            graph = stack.enter_context(patch("inference.handler.graph_check.assess_graph"))
            gnn = stack.enter_context(patch("inference.handler.gnn_check.assess_gnn"))
            rf.side_effect = lambda *_args: (
                call_order.append("rf") or component_unavailable("test_rf")
            )
            graph.side_effect = lambda *_args: (
                call_order.append("graph") or {
                    **component_unavailable("test_graph"),
                    "warning_flags": [
                        "[Graph] nominator is a super-nominator outlier"
                    ],
                }
            )
            gnn.side_effect = lambda *_args: (
                call_order.append("gnn") or component_unavailable("test_gnn")
            )

            save_decision = stack.enter_context(
                patch("inference.handler.db.save_integrity_decision_results")
            )
            save_decision.side_effect = lambda **_kwargs: call_order.append("decision")
            reject = stack.enter_context(patch("inference.handler.db.reject_nomination"))
            reject.side_effect = lambda *_args, **_kwargs: call_order.append("reject")
            publish = stack.enter_context(
                patch("inference.handler.service_bus_publisher.publish_event")
            )
            update_event = stack.enter_context(
                patch("inference.handler.db.update_processed_event_result")
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
            "GNN assessment starting",
            "GNN assessment completed",
            "IntegrityDecisionResults persisted",
            "Rules-based routing decision",
        ):
            self.assertIn(expected, messages)

    def test_gnn_explanation_is_published_after_routing_with_deterministic_id(self):
        call_order: list[str] = []
        details = {
            "tenant_id": 1,
            "nominator_id": 10,
            "beneficiary_id": 20,
            "description": "A specific contribution aligned to the category.",
            "category_description": "Going Above & Beyond",
            "amount": 2000,
        }
        gnn_result = {
            "model_available": True,
            "fraud_score": 72,
            "fraud_prob": 0.72,
            "risk_level": "HIGH",
            "warning_flags": [],
            "flagged": True,
            "model_version": "gnn-v2-test",
            "embedding_as_of": "2026-09-09",
            "graph_snapshot_id": "snapshot-test",
            "graph_snapshot_as_of": "2026-09-09",
            "feature_schema_version": "gnn-v2",
            "scoring_policy_version": 4,
            "_policy": {
                "explanation_enabled": True,
                "explanation_minimum_risk": "MEDIUM",
            },
        }

        with ExitStack() as stack:
            stack.enter_context(patch("inference.handler.db.claim_message", return_value=False))
            stack.enter_context(patch("inference.handler.db.get_nomination_details", return_value=details))
            stack.enter_context(patch("inference.handler.db.get_tenant_desc_check_config", return_value=object()))
            stack.enter_context(patch("inference.handler.db.get_integrity_component_statuses", return_value={}))
            stack.enter_context(patch(
                "inference.handler.description_check.check",
                return_value=description_check.CheckResult("pass", None, None),
            ))
            stack.enter_context(patch(
                "inference.handler.random_forest_check.assess",
                return_value=component_unavailable("test_rf"),
            ))
            stack.enter_context(patch(
                "inference.handler.graph_check.assess_graph",
                return_value=component_unavailable("test_graph"),
            ))
            stack.enter_context(patch(
                "inference.handler.gnn_check.assess_gnn",
                return_value=gnn_result,
            ))
            save_decision = stack.enter_context(patch(
                "inference.handler.db.save_integrity_decision_results",
                side_effect=lambda **_kwargs: call_order.append("decision"),
            ))
            stack.enter_context(patch(
                "inference.handler.db.set_nomination_status",
                side_effect=lambda *_args: call_order.append("route"),
            ))
            publish = stack.enter_context(patch(
                "inference.handler.service_bus_publisher.publish_event",
                side_effect=lambda event_type, *_args, **_kwargs: call_order.append(event_type),
            ))
            stack.enter_context(patch("inference.handler.db.update_processed_event_result"))

            handler.handle("source-message", {
                "event_type": "nomination.submitted",
                "nomination_id": 13881,
            })

        self.assertEqual(call_order, [
            "decision",
            "route",
            "nomination.fraud-flagged",
            "gnn.explanation.requested",
        ])
        persisted_gnn = save_decision.call_args.kwargs["engine_results"]["gnn"]
        self.assertEqual(persisted_gnn["explanation"]["status"], "REQUESTED")
        explanation_call = publish.call_args_list[-1]
        self.assertEqual(
            explanation_call.kwargs["message_id"],
            "gnnexp:t1:n13881:gnn-v2-test",
        )

if __name__ == "__main__":
    unittest.main()
