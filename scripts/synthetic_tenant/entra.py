"""Fail-closed Microsoft Entra provisioning for Synthetics Inc. identities."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import os
import secrets
import shutil
import string
import subprocess
import time
from typing import Any, Callable
from urllib.parse import quote

from .database import ORGANIZATION_ID
from .scenarios import SyntheticUser, UPN_DOMAIN


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
ADMIN_UPN = f"david64.terian@{UPN_DOMAIN}"
ADMIN_ROLE_VALUE = "AWard_Nomination_Admin"
AUTH_CLIENT_SECRET = "client-secret"
AUTH_AZURE_CLI = "azure-cli"


@dataclass(frozen=True)
class DirectoryResult:
    object_ids_by_upn: dict[str, str]
    created_count: int
    updated_count: int
    manager_count: int
    admin_role_assigned: bool


def _password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    return "".join(required + [secrets.choice(alphabet) for _ in range(28)])


def _approved_tenant_id() -> str:
    tenant_id = os.getenv("SYNTHETICS_AAD_TENANT_ID", ORGANIZATION_ID).strip()
    if tenant_id.lower() != ORGANIZATION_ID:
        raise EnvironmentError(
            "SYNTHETICS_AAD_TENANT_ID must exactly identify the approved organization"
        )
    return tenant_id


def _assert_token_tenant(token: str, tenant_id: str) -> None:
    """Reject a delegated token issued by any directory other than Synthetics Inc."""
    try:
        encoded_payload = token.split(".", 2)[1]
        encoded_payload += "=" * (-len(encoded_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload))
    except (IndexError, ValueError, binascii.Error) as exc:
        raise RuntimeError("Microsoft Graph returned an unreadable access token") from exc
    if str(payload.get("tid", "")).lower() != tenant_id.lower():
        raise RuntimeError("Microsoft Graph token was issued by the wrong Entra tenant")


def _token_from_client_secret(tenant_id: str) -> str:
    import msal

    client_id = os.getenv("SYNTHETICS_GRAPH_CLIENT_ID", "").strip()
    client_secret = os.getenv("SYNTHETICS_GRAPH_CLIENT_SECRET", "").strip()
    missing = [
        name
        for name, value in (
            ("SYNTHETICS_GRAPH_CLIENT_ID", client_id),
            ("SYNTHETICS_GRAPH_CLIENT_SECRET", client_secret),
        )
        if not value
    ]
    if missing:
        raise EnvironmentError(f"Missing Entra provisioning settings: {', '.join(missing)}")
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    token = result.get("access_token")
    if not token:
        raise RuntimeError(
            "Microsoft Graph token acquisition failed: "
            f"{result.get('error_description', result.get('error', 'unknown error'))}"
        )
    token = str(token)
    _assert_token_tenant(token, tenant_id)
    return token


def _token_from_azure_cli(tenant_id: str) -> str:
    """Use the operator's existing Azure CLI login without persisting a token."""
    az_executable = shutil.which("az")
    if not az_executable:
        raise EnvironmentError(
            "Azure CLI Graph authentication is unavailable; install Azure CLI and "
            f"run 'az login --tenant {tenant_id} --allow-no-subscriptions'"
        )
    try:
        completed = subprocess.run(
            [
                az_executable, "account", "get-access-token",
                "--tenant", tenant_id,
                "--resource-type", "ms-graph",
                "--query", "accessToken",
                "--output", "tsv",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise EnvironmentError(
            "Azure CLI Graph authentication is unavailable; install Azure CLI and "
            f"run 'az login --tenant {tenant_id} --allow-no-subscriptions'"
        ) from exc
    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        raise EnvironmentError(
            "Azure CLI has no usable Microsoft Graph login for the approved tenant; "
            f"run 'az login --tenant {tenant_id} --allow-no-subscriptions'"
        )
    _assert_token_tenant(token, tenant_id)
    return token


class GraphClient:
    """Minimal Graph client with bounded throttling and transient retry handling."""

    def __init__(
        self,
        token: str,
        *,
        session=None,
        sleeper: Callable[[float], None] = time.sleep,
        max_attempts: int = 6,
    ) -> None:
        import requests

        self.session = session or requests.Session()
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self.sleeper = sleeper
        self.max_attempts = max_attempts

    def request(
        self,
        method: str,
        path: str,
        *,
        expected: tuple[int, ...] = (200,),
        **kwargs,
    ):
        url = f"{GRAPH_BASE}/{path.lstrip('/')}"
        for attempt in range(1, self.max_attempts + 1):
            response = self.session.request(
                method, url, headers=self.headers, timeout=30, **kwargs
            )
            if response.status_code in expected:
                return response
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < self.max_attempts:
                raw_delay = response.headers.get("Retry-After", str(min(2**attempt, 30)))
                try:
                    delay = max(0.0, min(float(raw_delay), 60.0))
                except ValueError:
                    delay = float(min(2**attempt, 30))
                self.sleeper(delay)
                continue
            request_id = response.headers.get("request-id", "unavailable")
            raise RuntimeError(
                f"Microsoft Graph {method} {path} failed with HTTP "
                f"{response.status_code}; request-id={request_id}"
            )
        raise RuntimeError("Microsoft Graph retry loop exhausted")


def client_from_environment() -> GraphClient:
    tenant_id = _approved_tenant_id()
    auth_mode = os.getenv("SYNTHETICS_GRAPH_AUTH", "").strip().lower()
    if not auth_mode:
        has_client_credentials = bool(
            os.getenv("SYNTHETICS_GRAPH_CLIENT_ID", "").strip()
            and os.getenv("SYNTHETICS_GRAPH_CLIENT_SECRET", "").strip()
        )
        auth_mode = AUTH_CLIENT_SECRET if has_client_credentials else AUTH_AZURE_CLI
    if auth_mode == AUTH_CLIENT_SECRET:
        token = _token_from_client_secret(tenant_id)
    elif auth_mode == AUTH_AZURE_CLI:
        token = _token_from_azure_cli(tenant_id)
    else:
        raise EnvironmentError(
            "SYNTHETICS_GRAPH_AUTH must be 'client-secret' or 'azure-cli'"
        )
    return GraphClient(token)


def verify_domain(client: GraphClient) -> None:
    response = client.request("GET", f"domains/{quote(UPN_DOMAIN, safe='')}")
    payload = response.json()
    if str(payload.get("id", "")).lower() != UPN_DOMAIN or not payload.get("isVerified"):
        raise RuntimeError(f"{UPN_DOMAIN} is not verified in the approved Entra tenant")


def _desired_user(
    first_name: str,
    last_name: str,
    upn: str,
    department: str,
    *,
    enabled: bool,
) -> dict[str, Any]:
    return {
        "accountEnabled": enabled,
        "displayName": f"{first_name} {last_name}",
        "givenName": first_name,
        "surname": last_name,
        "mailNickname": upn.split("@", 1)[0],
        "userPrincipalName": upn,
        "department": department,
        "jobTitle": department,
        "usageLocation": "US",
    }


def _resolve_user(client: GraphClient, upn: str) -> dict[str, Any] | None:
    response = client.request(
        "GET",
        f"users/{quote(upn, safe='')}",
        expected=(200, 404),
        params={
            "$select": (
                "id,userPrincipalName,accountEnabled,displayName,givenName,surname,"
                "department,jobTitle,assignedLicenses"
            )
        },
    )
    return None if response.status_code == 404 else response.json()


def _reconcile_user(
    client: GraphClient,
    *,
    first_name: str,
    last_name: str,
    upn: str,
    department: str,
    enabled: bool,
    require_unlicensed: bool,
    initial_password: str | None = None,
    require_password_if_created: bool = False,
) -> tuple[str, bool]:
    desired = _desired_user(
        first_name, last_name, upn, department, enabled=enabled
    )
    existing = _resolve_user(client, upn)
    if existing is None:
        if require_password_if_created and not initial_password:
            raise EnvironmentError(
                f"{upn} does not exist; SYNTHETICS_ADMIN_INITIAL_PASSWORD is "
                "required to create an accessible administrator"
            )
        response = client.request(
            "POST",
            "users",
            expected=(201,),
            json={
                **desired,
                "passwordProfile": {
                    "forceChangePasswordNextSignIn": enabled,
                    "password": initial_password or _password(),
                },
            },
        )
        return str(response.json()["id"]), True

    if str(existing.get("userPrincipalName", "")).lower() != upn.lower():
        raise RuntimeError(f"Directory lookup returned a conflicting UPN for {upn}")
    if require_unlicensed and existing.get("assignedLicenses"):
        raise RuntimeError(f"Synthetic corpus identity {upn} unexpectedly has licenses")
    patch = {
        key: value
        for key, value in desired.items()
        if key != "userPrincipalName" and existing.get(key) != value
    }
    if patch:
        client.request("PATCH", f"users/{existing['id']}", expected=(204,), json=patch)
    return str(existing["id"]), False


def _set_manager(client: GraphClient, user_id: str, manager_id: str) -> None:
    client.request(
        "PUT",
        f"users/{user_id}/manager/$ref",
        expected=(204,),
        json={"@odata.id": f"{GRAPH_BASE}/users/{manager_id}"},
    )


def _assign_admin_role(client: GraphClient, admin_id: str) -> bool:
    service_principal_id = os.getenv(
        "SYNTHETICS_AWARD_SERVICE_PRINCIPAL_ID", ""
    ).strip()
    if not service_principal_id:
        raise EnvironmentError("SYNTHETICS_AWARD_SERVICE_PRINCIPAL_ID is required")
    service_principal = client.request(
        "GET",
        f"servicePrincipals/{service_principal_id}",
        params={"$select": "id,appRoles"},
    ).json()
    roles = [
        role
        for role in service_principal.get("appRoles", [])
        if role.get("value") == ADMIN_ROLE_VALUE and role.get("isEnabled", True)
    ]
    if len(roles) != 1:
        raise RuntimeError(
            f"Expected exactly one enabled {ADMIN_ROLE_VALUE} app role, found {len(roles)}"
        )
    role_id = str(roles[0]["id"])
    assignments = client.request(
        "GET",
        f"users/{admin_id}/appRoleAssignments",
        params={"$filter": f"resourceId eq {service_principal_id}"},
    ).json().get("value", [])
    if any(str(item.get("appRoleId")) == role_id for item in assignments):
        return False
    client.request(
        "POST",
        f"users/{admin_id}/appRoleAssignments",
        expected=(200, 201),
        json={
            "principalId": admin_id,
            "resourceId": service_principal_id,
            "appRoleId": role_id,
        },
    )
    return True


def provision_directory(
    client: GraphClient,
    users: list[SyntheticUser],
    *,
    progress: Callable[[str], None] | None = None,
) -> DirectoryResult:
    """Reconcile all 400 disabled corpus users and the enabled tenant admin."""
    report = progress or (lambda _message: None)
    verify_domain(client)
    service_principal_id = os.getenv(
        "SYNTHETICS_AWARD_SERVICE_PRINCIPAL_ID", ""
    ).strip()
    if not service_principal_id:
        raise EnvironmentError("SYNTHETICS_AWARD_SERVICE_PRINCIPAL_ID is required")
    initial_admin_password = os.getenv(
        "SYNTHETICS_ADMIN_INITIAL_PASSWORD", ""
    ).strip()
    if _resolve_user(client, ADMIN_UPN) is None and not initial_admin_password:
        raise EnvironmentError(
            f"{ADMIN_UPN} does not exist; SYNTHETICS_ADMIN_INITIAL_PASSWORD is "
            "required before creating any corpus identities"
        )
    report("Entra preflight passed")
    by_logical_id: dict[str, str] = {}
    by_upn: dict[str, str] = {}
    created = 0
    updated = 0
    for index, user in enumerate(users, start=1):
        object_id, was_created = _reconcile_user(
            client,
            first_name=user.first_name,
            last_name=user.last_name,
            upn=user.upn,
            department=user.department,
            enabled=False,
            require_unlicensed=True,
        )
        created += int(was_created)
        updated += int(not was_created)
        by_logical_id[user.logical_id] = object_id
        by_upn[user.upn] = object_id
        if index % 25 == 0 or index == len(users):
            report(f"Entra users reconciled: {index}/{len(users)}")

    manager_total = sum(user.manager_logical_id is not None for user in users)
    manager_index = 0
    for user in users:
        if user.manager_logical_id is not None:
            _set_manager(
                client,
                by_logical_id[user.logical_id],
                by_logical_id[user.manager_logical_id],
            )
            manager_index += 1
            if manager_index % 50 == 0 or manager_index == manager_total:
                report(f"Entra managers reconciled: {manager_index}/{manager_total}")

    admin_id, admin_created = _reconcile_user(
        client,
        first_name="David64",
        last_name="Terian",
        upn=ADMIN_UPN,
        department="Executive Leadership",
        enabled=True,
        require_unlicensed=False,
        initial_password=initial_admin_password,
        require_password_if_created=True,
    )
    created += int(admin_created)
    updated += int(not admin_created)
    by_upn[ADMIN_UPN] = admin_id
    role_assigned = _assign_admin_role(client, admin_id)
    report("Entra administrator and application role reconciled")
    return DirectoryResult(
        object_ids_by_upn=by_upn,
        created_count=created,
        updated_count=updated,
        manager_count=manager_total,
        admin_role_assigned=role_assigned,
    )
