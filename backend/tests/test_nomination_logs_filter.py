"""Tests for the Nomination Logs drawer's server-side logger filter.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\backend"
    python -m unittest discover -s tests -p "test_nomination_logs_filter.py" -v

The database is mocked; this test verifies that the API forwards the drawer's
"Integrity check only" selection to the SQL helper and reports the active mode.
"""

import os
import unittest
from datetime import datetime
from unittest.mock import patch


os.environ.setdefault("CLIENT_ID", "unit-test-client")

from routers.admin_router import get_nomination_logs


class NominationLogsFilterTests(unittest.IsolatedAsyncioTestCase):
    @patch("routers.admin_router.sqlhelper.get_nomination_logs")
    async def test_integrity_check_only_is_forwarded_to_sql_helper(
        self, get_logs
    ):
        get_logs.return_value = [
            (
                datetime(2026, 8, 25, 1, 15, 52),
                "INFO",
                "award-integrity-check-sandbox",
                "integrity_check.random_forest",
                "RF assessment completed",
                '{"fraud_score": 7}',
            )
        ]

        result = await get_nomination_logs(
            nomination_id=13866,
            integrity_check_only=True,
            user_context={
                "actual_user": {"roles": ["AWard_Nomination_Admin"]},
                "effective_user": {"UserId": 7, "TenantId": 3},
            },
        )

        get_logs.assert_called_once_with(13866, 3, integrity_check_only=True)
        self.assertTrue(result["integrity_check_only"])
        self.assertEqual(result["log_count"], 1)
        self.assertEqual(result["logs"][0]["logger"], "integrity_check.random_forest")


if __name__ == "__main__":
    unittest.main()
