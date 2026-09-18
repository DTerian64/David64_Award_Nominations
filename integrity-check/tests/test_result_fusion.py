"""Tests for the independent inference-result fusion policy."""

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import result_fusion


def component(risk="NONE", score=0, available=True, probability=0.0, flags=None):
    return {
        "model_available": available,
        "risk_level": risk,
        "fraud_score": score,
        "fraud_prob": probability,
        "warning_flags": flags or [],
    }


class ResultFusionTests(unittest.TestCase):
    def test_available_gnn_always_participates(self):
        decision = result_fusion.combine(
            component("LOW", 30),
            component("MEDIUM", 50),
            component("CRITICAL", 99),
        )
        self.assertEqual(decision["risk_level"], "CRITICAL")
        self.assertEqual(decision["decisive_models"], ["GNN"])

    def test_gnn_can_elevate_route(self):
        decision = result_fusion.combine(
            component("LOW", 30),
            component("NONE", 0, probability=None),
            component("HIGH", 72),
        )
        self.assertEqual(decision["risk_level"], "HIGH")
        self.assertEqual(decision["decisive_models"], ["GNN"])
        self.assertTrue(decision["flagged"])

    def test_risk_category_wins_over_incomparable_numeric_scores(self):
        decision = result_fusion.combine(
            component("MEDIUM", 59),
            component("HIGH", 75, probability=None),
            component(available=False),
        )
        self.assertEqual(decision["risk_level"], "HIGH")
        self.assertEqual(decision["final_score"], 75)

    def test_no_available_opinion_is_unknown_not_clean(self):
        decision = result_fusion.combine(
            component(available=False),
            component(available=False),
            component(available=False),
        )
        self.assertFalse(decision["decision_available"])
        self.assertEqual(decision["risk_level"], "UNKNOWN")

    def test_tabular_mlp_is_not_mislabeled_as_random_forest(self):
        tabular = component("HIGH", 70, flags=["Elevated pair activity"])
        tabular["architecture"] = "tabular_mlp"

        decision = result_fusion.combine(
            tabular,
            component(available=False),
            component(available=False),
        )

        self.assertEqual(decision["participating_models"], ["TABULAR_MLP"])
        self.assertEqual(decision["decisive_models"], ["TABULAR_MLP"])
        self.assertEqual(
            decision["warning_flags"],
            ["[TABULAR_MLP] Elevated pair activity"],
        )


if __name__ == "__main__":
    unittest.main()
