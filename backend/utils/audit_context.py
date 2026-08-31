"""
utils/audit_context.py — request-scoped actor for SOC 2 audit columns.

The auth dependency sets `current_actor` to the effective UPN (impersonation-
aware) once per request; utils/sqlhelper2.py reads it via get_actor() when
stamping created_by / updated_by on key-table writes. Writes that happen
outside an authenticated request (startup, background tasks, unauthenticated
endpoints) fall back to 'system'. Service-to-service writes use their own
'svc:<name>' markers in the worker containers, not this module.
"""

import contextvars

_SYSTEM = "system"

current_actor: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_actor", default=_SYSTEM
)


def set_actor(actor: str | None) -> None:
    """Set the current request's audit actor (effective UPN)."""
    current_actor.set(actor or _SYSTEM)


def get_actor() -> str:
    """Return the current request's audit actor, or 'system' if unset."""
    return current_actor.get()
