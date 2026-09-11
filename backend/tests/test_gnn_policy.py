"""GNN policy workflow and validation tests."""

import json
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

os.environ.setdefault("CLIENT_ID", "unit-test-client")

from routers.setup_router import (
    GNNPolicyDraft,
    GraphThresholds,
    _validate_gnn_policy,
    create_gnn_policy_draft,
    publish_gnn_policy_draft,
)
from utils.sqlhelper2 import _gnn_configuration_from_payload, _gnn_policy_row


def policy(**changes):
    values = {
        "training_enabled": True,
        "inference_enabled": True,
        "hidden_dim": 64,
        "embed_dim": 64,
        "epochs": 300,
        "rolling_folds": 3,
        "window_days": 180,
        "embedding_retention_days": 90,
        "stale_embedding_days": 14,
        "minimum_training_samples": 300,
        "minimum_users": 50,
        "minimum_positives_per_split": 10,
        "candidate_architectures": ["graphsage", "gcn", "gatv2"],
        "selection_metric": "holdout_pr_auc",
        "minimum_improvement_over_mlp": 0.02,
        "incumbent_tie_tolerance": 0.01,
        "minimum_eligible_graph_candidates": 2,
        "thresholds": GraphThresholds(
            low=25, medium=45, high=65, critical=85
        ),
        "explanation_enabled": False,
        "explanation_minimum_risk": "MEDIUM",
    }
    values.update(changes)
    return GNNPolicyDraft(**values)


class GNNPolicyValidationTests(unittest.TestCase):
    def test_valid_policy_is_accepted(self):
        _validate_gnn_policy(policy())

    def test_candidates_must_be_supported_and_unique(self):
        for candidates in (["graphsage", "graphsage"], ["graphsage", "tgn"]):
            with self.assertRaises(HTTPException):
                _validate_gnn_policy(policy(candidate_architectures=candidates))

    def test_minimum_eligible_cannot_exceed_candidate_count(self):
        with self.assertRaises(HTTPException):
            _validate_gnn_policy(policy(
                candidate_architectures=["graphsage"],
                minimum_eligible_graph_candidates=2,
            ))

    def test_retention_must_cover_staleness_window(self):
        with self.assertRaises(HTTPException):
            _validate_gnn_policy(policy(
                embedding_retention_days=7, stale_embedding_days=14
            ))

    def test_configuration_json_round_trip_preserves_api_contract(self):
        payload = policy().model_dump()
        configuration = _gnn_configuration_from_payload(payload)

        self.assertEqual(configuration["schema_version"], 1)
        self.assertEqual(
            configuration["architecture_selection"]["candidate_architectures"],
            ["graphsage", "gcn", "gatv2"],
        )
        row = (
            41, 3, "ACTIVE", True, True,
            json.dumps(configuration), False, "MEDIUM",
            None, "creator", None, "updater", None, None,
        )

        restored = _gnn_policy_row(row)

        self.assertEqual(restored["hidden_dim"], payload["hidden_dim"])
        self.assertEqual(restored["thresholds"], payload["thresholds"])
        self.assertEqual(
            restored["candidate_architectures"], payload["candidate_architectures"]
        )


class GNNPolicyEndpointTests(unittest.IsolatedAsyncioTestCase):
    @patch("routers.setup_router.sqlhelper.create_gnn_scoring_policy_draft")
    async def test_create_draft_uses_admin_tenant(self, create_draft):
        create_draft.return_value = 44
        result = await create_gnn_policy_draft({
            "TenantId": 7, "userPrincipalName": "admin@example.com",
        })
        create_draft.assert_called_once_with(7, "admin@example.com")
        self.assertEqual(result["policy_id"], 44)

    @patch("routers.setup_router.sqlhelper.publish_gnn_scoring_policy_draft")
    async def test_publish_uses_admin_tenant(self, publish):
        publish.return_value = 45
        result = await publish_gnn_policy_draft({
            "TenantId": 7, "userPrincipalName": "admin@example.com",
        })
        publish.assert_called_once_with(7, "admin@example.com")
        self.assertEqual(result["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
