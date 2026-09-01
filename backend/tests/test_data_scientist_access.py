"""Role and tenant-boundary tests for the Data Scientist workspace."""

import os
import unittest
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

from fastapi import HTTPException


os.environ.setdefault("CLIENT_ID", "unit-test-client")

from auth import require_analytics_access
from routers.model_analysis_router import (
    GraphFineTuningRequest,
    get_decision_engines_setup,
    get_fraud_integrity_setup,
    get_graph_scoring_policy,
    get_model_manifest,
    get_nomination_analysis,
    get_rf_model_visualization,
    search_nominations,
    request_graph_scoring_change,
)
from utils import sqlhelper2


class DataScientistAuthorizationTests(unittest.TestCase):
    @patch("auth.sqlhelper.get_user_roles", return_value=["DataScientist"])
    def test_database_role_grants_analytics_access(self, _get_roles):
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        self.assertIs(require_analytics_access(context), context)

    @patch("auth.sqlhelper.get_user_roles", return_value=[])
    def test_unprivileged_user_is_rejected(self, _get_roles):
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        with self.assertRaises(HTTPException) as raised:
            require_analytics_access(context)
        self.assertEqual(raised.exception.status_code, 403)


class ModelAnalysisEndpointTests(unittest.IsolatedAsyncioTestCase):
    @patch("routers.model_analysis_router.model_artifacts.get_manifest")
    async def test_model_manifest_is_read_from_effective_tenant(self, get_manifest):
        get_manifest.return_value = {
            "schema_version": 1,
            "artifact_type": "random_forest",
            "tenant_id": 4,
        }
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        result = await get_model_manifest("rf", context)
        get_manifest.assert_called_once_with(4, "rf")
        self.assertTrue(result["available"])

    @patch("routers.model_analysis_router.model_artifacts.get_manifest")
    async def test_missing_manifest_returns_a_training_run_message(self, get_manifest):
        get_manifest.return_value = None
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        result = await get_model_manifest("gnn", context)
        self.assertFalse(result["available"])
        self.assertIn("next training run", result["message"])

    @patch("routers.model_analysis_router.model_artifacts.get_rf_visualization")
    async def test_rf_visualization_is_read_from_effective_tenant(self, get_image):
        get_image.return_value = b"\x89PNG\r\n"
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        response = await get_rf_model_visualization(context)
        get_image.assert_called_once_with(4)
        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(response.body, b"\x89PNG\r\n")

    @patch("routers.model_analysis_router.sqlhelper.get_fraud_settings")
    async def test_fraud_setup_is_read_from_effective_tenant(self, get_settings):
        get_settings.return_value = {"low_threshold": 20}
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        result = await get_fraud_integrity_setup(context)
        get_settings.assert_called_once_with(4)
        self.assertEqual(result["low_threshold"], 20)

    @patch("routers.model_analysis_router.sqlhelper.get_integrity_component_statuses")
    async def test_decision_engines_are_read_from_effective_tenant(self, get_statuses):
        get_statuses.return_value = [{"component": "RF"}]
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        result = await get_decision_engines_setup(context)
        get_statuses.assert_called_once_with(4)
        self.assertEqual(result["rows"][0]["component"], "RF")

    @patch("routers.model_analysis_router.sqlhelper.get_graph_scoring_policy_bundle")
    async def test_data_scientist_can_inspect_but_not_edit_policy(self, get_policy):
        get_policy.return_value = {
            "active_policy": {"policy_version": 2},
            "draft_policy": {"policy_version": 3},
            "history": [
                {"policy_version": 3, "status": "DRAFT"},
                {"policy_version": 2, "status": "ACTIVE"},
            ],
        }
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
            "is_impersonating": False,
        }
        result = await get_graph_scoring_policy(context)
        get_policy.assert_called_once_with(4)
        self.assertFalse(result["can_edit"])
        self.assertIsNone(result["draft_policy"])
        self.assertEqual([item["policy_version"] for item in result["history"]], [2])

    @patch("routers.model_analysis_router.sqlhelper.create_graph_scoring_change_request")
    async def test_fine_tuning_request_is_tenant_scoped(self, create_request):
        create_request.return_value = 81
        context = {
            "actual_user": {"roles": []},
            "effective_user": {
                "UserId": 19, "TenantId": 4,
                "userPrincipalName": "scientist@example.com",
            },
            "is_impersonating": False,
        }
        result = await request_graph_scoring_change(
            GraphFineTuningRequest(
                pattern_type="Ring",
                request_text="Review false positives",
                supporting_nomination_ids=[12, 12, 15],
            ),
            context,
        )
        self.assertEqual(result["request_id"], 81)
        self.assertEqual(
            create_request.call_args.kwargs["supporting_nomination_ids"],
            [12, 15],
        )

    async def test_fine_tuning_request_is_blocked_during_impersonation(self):
        context = {
            "actual_user": {"roles": ["AWard_Nomination_Admin"]},
            "effective_user": {
                "UserId": 19, "TenantId": 4,
                "userPrincipalName": "scientist@example.com",
            },
            "is_impersonating": True,
        }
        with self.assertRaises(HTTPException) as raised:
            await request_graph_scoring_change(
                GraphFineTuningRequest(request_text="Change Ring scoring"),
                context,
            )
        self.assertEqual(raised.exception.status_code, 403)

    @patch("routers.model_analysis_router.sqlhelper.search_model_analysis_nominations")
    async def test_search_uses_effective_tenant(self, search):
        search.return_value = {"items": [], "total": 0, "page": 1, "page_size": 25}
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        result = await search_nominations(
            q="Ada", nomination_status=None, risk="HIGH",
            start_date=date(2026, 8, 1), end_date=date(2026, 8, 31),
            page=1, page_size=25,
            user_context=context,
        )
        search.assert_called_once_with(
            tenant_id=4, query="Ada", status_filter=None, risk_filter="HIGH",
            start_date=date(2026, 8, 1), end_date=date(2026, 8, 31),
            page=1, page_size=25,
        )
        self.assertEqual(result["total"], 0)

    async def test_search_rejects_an_inverted_date_range(self):
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        with self.assertRaises(HTTPException) as raised:
            await search_nominations(
                q="", nomination_status=None, risk=None,
                start_date=date(2026, 9, 1), end_date=date(2026, 8, 31),
                page=1, page_size=25, user_context=context,
            )
        self.assertEqual(raised.exception.status_code, 422)

    @patch("routers.model_analysis_router.sqlhelper.get_model_analysis_nomination")
    async def test_detail_uses_effective_tenant(self, get_detail):
        get_detail.return_value = {"nomination_id": 123}
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        result = await get_nomination_analysis(123, context)
        get_detail.assert_called_once_with(123, 4)
        self.assertEqual(result["nomination_id"], 123)


class _MergeResult:
    rowcount = 1


class _RoleSession:
    def __init__(self):
        self.statement = ""
        self.params = None
        self.committed = False

    def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return _MergeResult()

    def commit(self):
        self.committed = True


@contextmanager
def _role_session_context(session):
    yield session


class DataScientistRolePersistenceTests(unittest.TestCase):
    def test_role_grant_inserts_required_tenant_id(self):
        session = _RoleSession()
        with (
            patch(
                "utils.sqlhelper2.get_db_context",
                return_value=_role_session_context(session),
            ),
            patch("utils.sqlhelper2.get_actor", return_value="admin@example.com"),
        ):
            inserted = sqlhelper2.assign_user_role(
                user_id=421,
                role="DataScientist",
                assigned_by=512,
                tenant_id=3,
            )

        self.assertTrue(inserted)
        self.assertTrue(session.committed)
        self.assertEqual(session.params["tenant_id"], 3)
        self.assertIn("INSERT (UserId, TenantId, Role", session.statement)


if __name__ == "__main__":
    unittest.main()
