"""Graph scoring policy workflow and validation tests."""

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

os.environ.setdefault("CLIENT_ID", "unit-test-client")

from routers.setup_router import (
    GraphPatternPolicy,
    GraphPolicyDraft,
    GraphThresholds,
    _validate_graph_policy,
    create_graph_policy_draft,
    publish_graph_policy_draft,
)


def _patterns():
    names = [
        "Ring", "SuperNominator", "Desert", "CopyPaste",
        "TransactionalLanguage", "HiddenCandidate",
    ]
    return [
        GraphPatternPolicy(
            pattern_type=name,
            enabled=True,
            enabled_for_routing=name not in {"Desert", "HiddenCandidate"},
            applicable_roles=["nominator"],
            base_score=30,
            minimum_score=0,
            maximum_score=100,
            parameters={"evidence_weight": 50},
        )
        for name in names
    ]


class GraphPolicyValidationTests(unittest.TestCase):
    def test_valid_policy_is_accepted(self):
        _validate_graph_policy(GraphPolicyDraft(
            thresholds=GraphThresholds(low=25, medium=50, high=75, critical=90),
            detection_window_days=365,
            snapshot_max_age_days=14,
            patterns=_patterns(),
        ))

    def test_routing_detector_must_be_enabled(self):
        patterns = _patterns()
        patterns[0].enabled = False
        with self.assertRaises(HTTPException) as raised:
            _validate_graph_policy(GraphPolicyDraft(
                thresholds=GraphThresholds(low=25, medium=50, high=75, critical=90),
                detection_window_days=365,
                snapshot_max_age_days=14,
                patterns=patterns,
            ))
        self.assertEqual(raised.exception.status_code, 422)


class GraphPolicyEndpointTests(unittest.IsolatedAsyncioTestCase):
    @patch("routers.setup_router.sqlhelper.create_graph_scoring_policy_draft")
    async def test_create_draft_uses_admin_tenant(self, create_draft):
        create_draft.return_value = 44
        result = await create_graph_policy_draft({
            "TenantId": 7, "userPrincipalName": "admin@example.com",
        })
        create_draft.assert_called_once_with(7, "admin@example.com")
        self.assertEqual(result["policy_id"], 44)

    @patch("routers.setup_router.sqlhelper.publish_graph_scoring_policy_draft")
    async def test_publish_uses_admin_tenant(self, publish):
        publish.return_value = 45
        result = await publish_graph_policy_draft({
            "TenantId": 7, "userPrincipalName": "admin@example.com",
        })
        publish.assert_called_once_with(7, "admin@example.com")
        self.assertEqual(result["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
