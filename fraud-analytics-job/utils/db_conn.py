"""Deprecated compatibility import for Integrity Sentinel connections.

New code must import either ``integrity_sentinel.db`` or the source adapter's
connection module so the database boundary is explicit.
"""

from integrity_sentinel.db import RenewableConnection, connect

__all__ = ["RenewableConnection", "connect"]
