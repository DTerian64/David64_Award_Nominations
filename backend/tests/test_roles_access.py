"""Tests for Setup > Roles & Access user-picker data.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\backend"
    python -m unittest discover -s tests -p "test_roles_access.py" -v
"""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from utils import sqlhelper2


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.params = None
        self.statement = None

    def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return _Result(self.rows)


@contextmanager
def _session_context(session):
    yield session


class RolesAccessSqlTests(unittest.TestCase):
    def test_role_picker_users_include_title(self):
        session = _Session([
            (41, "Ani", "Hakobyan", "ani.hakobyan@example.com", "Finance"),
        ])

        with patch(
            "utils.sqlhelper2.get_db_context",
            return_value=_session_context(session),
        ):
            users = sqlhelper2.get_tenant_users_brief(tenant_id=5)

        self.assertIn("Title", session.statement)
        self.assertEqual(session.params, {"tid": 5})
        self.assertEqual(users, [{
            "user_id": 41,
            "name": "Ani Hakobyan",
            "upn": "ani.hakobyan@example.com",
            "title": "Finance",
        }])


if __name__ == "__main__":
    unittest.main()
