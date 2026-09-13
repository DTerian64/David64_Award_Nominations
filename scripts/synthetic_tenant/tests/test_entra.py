"""Microsoft Graph retry and request-safety tests for synthetic provisioning."""

import base64
from dataclasses import dataclass, field
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.synthetic_tenant.database import ORGANIZATION_ID
from scripts.synthetic_tenant.entra import (
    GraphClient,
    _reconcile_user,
    _token_from_azure_cli,
)


def _token(tenant_id: str) -> str:
    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode({'tid': tenant_id})}.signature"


@dataclass
class Response:
    status_code: int
    payload: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)


def test_graph_client_honors_retry_after_without_exposing_response_body():
    session = Session([
        Response(429, {"secret": "must-not-appear"}, {"Retry-After": "2"}),
        Response(200, {"ok": True}),
    ])
    sleeps = []
    client = GraphClient("token", session=session, sleeper=sleeps.append)

    result = client.request("GET", "domains/example.test")

    assert result.json() == {"ok": True}
    assert sleeps == [2.0]
    assert len(session.calls) == 2


def test_graph_error_reports_status_and_request_id_but_not_response_body():
    session = Session([
        Response(400, {"secret": "must-not-appear"}, {"request-id": "request-7"})
    ])
    client = GraphClient("token", session=session, sleeper=lambda _: None)

    with pytest.raises(RuntimeError) as error:
        client.request("POST", "users", expected=(201,), json={"x": 1})

    assert "HTTP 400" in str(error.value)
    assert "request-7" in str(error.value)
    assert "must-not-appear" not in str(error.value)


def test_new_enabled_admin_requires_an_explicit_initial_password():
    client = GraphClient("token", session=Session([]), sleeper=lambda _: None)
    with patch("scripts.synthetic_tenant.entra._resolve_user", return_value=None):
        with pytest.raises(EnvironmentError, match="SYNTHETICS_ADMIN_INITIAL_PASSWORD"):
            _reconcile_user(
                client,
                first_name="David64",
                last_name="Terian",
                upn="david64.terian@synthetics.terian-services.com",
                department="Executive Leadership",
                enabled=True,
                require_unlicensed=False,
                require_password_if_created=True,
            )


def test_azure_cli_token_is_requested_for_the_approved_tenant():
    completed = SimpleNamespace(
        returncode=0,
        stdout=f"{_token(ORGANIZATION_ID)}\n",
        stderr="",
    )
    with (
        patch("scripts.synthetic_tenant.entra.shutil.which", return_value="az.cmd"),
        patch(
            "scripts.synthetic_tenant.entra.subprocess.run", return_value=completed
        ) as run,
    ):
        result = _token_from_azure_cli(ORGANIZATION_ID)

    assert result == _token(ORGANIZATION_ID)
    command = run.call_args.args[0]
    assert command[command.index("--tenant") + 1] == ORGANIZATION_ID
    assert command[command.index("--resource-type") + 1] == "ms-graph"


def test_azure_cli_token_from_another_tenant_is_rejected():
    completed = SimpleNamespace(
        returncode=0,
        stdout=f"{_token('00000000-0000-0000-0000-000000000000')}\n",
        stderr="",
    )
    with (
        patch("scripts.synthetic_tenant.entra.shutil.which", return_value="az.cmd"),
        patch("scripts.synthetic_tenant.entra.subprocess.run", return_value=completed),
    ):
        with pytest.raises(RuntimeError, match="wrong Entra tenant"):
            _token_from_azure_cli(ORGANIZATION_ID)
