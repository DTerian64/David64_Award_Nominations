"""Role and tenant-boundary tests for the Data Scientist workspace."""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException


os.environ.setdefault("CLIENT_ID", "unit-test-client")

from auth import require_analytics_access
from routers.model_analysis_router import get_nomination_analysis, search_nominations
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
    @patch("routers.model_analysis_router.sqlhelper.search_model_analysis_nominations")
    async def test_search_uses_effective_tenant(self, search):
        search.return_value = {"items": [], "total": 0, "page": 1, "page_size": 25}
        context = {
            "actual_user": {"roles": []},
            "effective_user": {"UserId": 19, "TenantId": 4},
        }
        result = await search_nominations(
            q="Ada", nomination_status=None, risk="HIGH", page=1, page_size=25,
            user_context=context,
        )
        search.assert_called_once_with(
            tenant_id=4, query="Ada", status_filter=None, risk_filter="HIGH",
            page=1, page_size=25,
        )
        self.assertEqual(result["total"], 0)

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
