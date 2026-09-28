"""Tenant control for the scheduled integrity analytics job."""

import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("CLIENT_ID", "unit-test-client")

from routers.setup_router import (
    AnalyticsJobUpdate,
    get_analytics_job,
    require_setup_admin,
    update_analytics_job,
)
from utils import sqlhelper2 as sql


def _session(monkeypatch, row, update_rowcount=1):
    session = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = session
    selected = MagicMock()
    selected.fetchone.return_value = row
    updated = MagicMock()
    updated.rowcount = update_rowcount
    session.execute.side_effect = [selected, updated]
    monkeypatch.setattr(sql, "get_db_context", lambda: context)
    return session


@pytest.mark.parametrize("raw", [None, "not-json", "[]", '{"integrity_analytics_job":{"enabled":"false"}}'])
def test_missing_or_malformed_setting_defaults_to_enabled(monkeypatch, raw):
    session = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = session
    session.execute.return_value.fetchone.return_value = (raw,)
    monkeypatch.setattr(sql, "get_db_context", lambda: context)

    result = sql.get_integrity_analytics_job_settings(5)

    assert result == {
        "enabled": True,
        "state": "ENABLED",
        "updated_at": None,
        "updated_by": None,
        "takes_effect": "before_next_unclaimed_tenant",
    }


def test_existing_paused_setting_returns_its_own_audit_metadata(monkeypatch):
    raw = json.dumps({
        "integrity_analytics_job": {
            "enabled": False,
            "updated_at": "2026-09-28T18:30:00Z",
            "updated_by": "admin@example.com",
        },
    })
    session = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = session
    session.execute.return_value.fetchone.return_value = (raw,)
    monkeypatch.setattr(sql, "get_db_context", lambda: context)

    result = sql.get_integrity_analytics_job_settings(5)

    assert result["enabled"] is False
    assert result["state"] == "PAUSED"
    assert result["updated_by"] == "admin@example.com"
    assert result["updated_at"] == "2026-09-28T18:30:00Z"


def test_update_preserves_unrelated_integrity_configuration(monkeypatch):
    existing = {
        "graph_pattern": {"detection_window_days": 180},
        "gnn": {"window_days": 365},
        "unrelated": {"preserve": True},
        "fraud_analytics_job": {"enabled": True},
    }
    session = _session(monkeypatch, (json.dumps(existing),))

    sql.update_integrity_analytics_job_settings(5, False, "admin@example.com")

    written = json.loads(session.execute.call_args_list[1].args[1]["configuration"])
    assert written["graph_pattern"] == existing["graph_pattern"]
    assert written["gnn"] == existing["gnn"]
    assert written["unrelated"] == existing["unrelated"]
    assert "fraud_analytics_job" not in written
    assert written["integrity_analytics_job"]["enabled"] is False
    assert written["integrity_analytics_job"]["updated_by"] == "admin@example.com"
    assert written["integrity_analytics_job"]["updated_at"].endswith("Z")
    session.commit.assert_called_once()


def test_update_rejects_unknown_tenant(monkeypatch):
    session = _session(monkeypatch, (None,), update_rowcount=0)

    with pytest.raises(ValueError, match="Tenant 999 was not found"):
        sql.update_integrity_analytics_job_settings(999, False, "admin@example.com")

    session.commit.assert_not_called()


def test_update_payload_requires_a_real_boolean():
    with pytest.raises(ValidationError):
        AnalyticsJobUpdate(enabled="false")


def test_routes_use_authenticated_admin_tenant_and_actor():
    admin = {
        "TenantId": 7,
        "userPrincipalName": "admin@example.com",
        "roles": ["AWard_Nomination_Admin"],
    }
    response = {
        "enabled": False,
        "state": "PAUSED",
        "updated_at": None,
        "updated_by": "admin@example.com",
        "takes_effect": "before_next_unclaimed_tenant",
    }
    with patch(
        "routers.setup_router.sqlhelper.update_integrity_analytics_job_settings"
    ) as update, patch(
        "routers.setup_router.sqlhelper.get_integrity_analytics_job_settings",
        return_value=response,
    ) as get:
        result = asyncio.run(update_analytics_job(AnalyticsJobUpdate(enabled=False), admin))

    update.assert_called_once_with(7, False, "admin@example.com")
    get.assert_called_once_with(7)
    assert result == response


def test_get_route_uses_authenticated_admin_tenant():
    admin = {"TenantId": 7, "roles": ["AWard_Nomination_Admin"]}
    with patch(
        "routers.setup_router.sqlhelper.get_integrity_analytics_job_settings",
        return_value={"enabled": True},
    ) as get:
        assert asyncio.run(get_analytics_job(admin)) == {"enabled": True}
    get.assert_called_once_with(7)


def test_setup_guard_rejects_non_admin_and_impersonation():
    with pytest.raises(HTTPException) as non_admin:
        asyncio.run(require_setup_admin({"roles": []}, None))
    assert non_admin.value.status_code == 403

    with pytest.raises(HTTPException) as impersonating:
        asyncio.run(require_setup_admin(
            {"roles": ["AWard_Nomination_Admin"]}, "other@example.com"
        ))
    assert impersonating.value.status_code == 403
