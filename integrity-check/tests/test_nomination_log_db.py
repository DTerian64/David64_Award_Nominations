"""Regression tests for nomination-log SQL parameter binding.

Purpose:
    Ensure nomination-log JSON larger than 255 Unicode characters is sent using
    standard pyodbc execution. Enabling fast_executemany caused ODBC to bind a
    510-byte buffer and reject larger RF/SHAP details with HY000 truncation.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m unittest tests.test_nomination_log_db -v
"""

import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")

from utils import db


class _Cursor:
    def __init__(self):
        self.fast_executemany = False
        self.sql = None
        self.params = None

    def executemany(self, sql, params):
        self.sql = sql
        self.params = params


class NominationLogDBTests(unittest.TestCase):
    def test_long_details_do_not_enable_fast_executemany(self):
        cursor = _Cursor()
        connection = MagicMock()
        connection.cursor.return_value = cursor
        connection_manager = MagicMock()
        connection_manager.__enter__.return_value = connection
        long_details = '{"top_features":"' + ("x" * 1_000) + '"}'
        row = {
            "nomination_id": 13875,
            "tenant_id": 3,
            "log_time": datetime(2026, 8, 26, 0, 31, 54),
            "level": "INFO",
            "service": "integrity-check-test",
            "logger": "integrity_check.random_forest",
            "message": "RF SHAP assessment completed",
            "message_id": "test-message",
            "details": long_details,
            "exception": None,
            "created_by": "svc:test",
            "updated_by": "svc:test",
        }

        with patch.object(db, "_get_conn", return_value=connection_manager):
            db.insert_nomination_logs([row])

        self.assertFalse(cursor.fast_executemany)
        self.assertEqual(cursor.params[0][9], long_details)
        connection.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
