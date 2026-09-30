"""Connection to Integrity Sentinel's ``integrity`` and ``ops`` storage."""

from __future__ import annotations

import pyodbc

from utils.sql_connection import RenewableConnection as _RenewableConnection
from utils.sql_connection import connect_from_environment


def connect(timeout: int = 60) -> pyodbc.Connection:
    """Open the Integrity Sentinel database configured by ``IS_SQL_*``."""

    return connect_from_environment("IS_SQL_SERVER", "IS_SQL_DATABASE", timeout)


class RenewableConnection(_RenewableConnection):
    """Renewable connection whose default factory targets Integrity Sentinel."""

    def __init__(self, factory=connect, *, log_context: str = "Integrity Sentinel"):
        super().__init__(factory, log_context=log_context)
