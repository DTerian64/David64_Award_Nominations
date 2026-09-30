"""Connection factory for the Award Nominations source database."""

from __future__ import annotations

import pyodbc

from utils.sql_connection import connect_from_environment


def connect(timeout: int = 60) -> pyodbc.Connection:
    """Open the Award Nominations database configured by ``AWARD_SQL_*``."""

    return connect_from_environment(
        "AWARD_SQL_SERVER",
        "AWARD_SQL_DATABASE",
        timeout,
    )
