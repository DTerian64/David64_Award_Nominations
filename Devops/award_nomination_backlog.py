#!/usr/bin/env python3
"""
Award Nomination – Azure DevOps Backlog Creator
================================================
Creates 10 Epics → Features → User Stories in
  https://dev.azure.com/Terian-Services/Award_Nomination  (Agile process)

Idempotent: existing work items (matched by title) are skipped so the
script can be re-run safely without creating duplicates.

Authentication (tries in order):
  1. ADO_PAT environment variable  ($env:ADO_PAT = "..." in PowerShell)
  2. Azure CLI session             (az login already done — no extra setup)
  3. Interactive PAT prompt

Usage
-----
    python award_nomination_backlog.py
"""

import os, sys, time, base64, json, subprocess
import urllib.request, urllib.error

ORG      = "Terian-Services"
PROJECT  = "Award_Nomination"
API_VER  = "7.1"
BASE_URL = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis"

# ── Backlog  (Epic → Feature → User Story) ────────────────────────────────────
#
# Structure:
#   BACKLOG = [
#     { "epic": "...", "description": "...",
#       "features": [
#         { "feature": "...", "description": "...",
#           "stories": [ ("title", "acceptance criteria"), ... ]
#         }, ...
#       ]
#     }, ...
#   ]

BACKLOG = [
    # ── 1 ─────────────────────────────────────────────────────────────────────
    {
        "epic": "Core Nomination Workflow",
        "description": (
            "End-to-end nomination lifecycle: creating nominations, manager approval, "
            "payroll extract generation, and nomination history views."
        ),
        "features": [
            {
                "feature": "Nomination Submission",
                "description": "Allow employees to submit monetary award nominations for colleagues.",
                "stories": [
                    (
                        "Submit a nomination",
                        "As an employee, I can nominate a colleague for a monetary award by "
                        "selecting them from a list, specifying an amount and currency, and "
                        "providing a justification, so that deserving colleagues are recognised.",
                    ),
                ],
            },
            {
                "feature": "Approval Management",
                "description": "Enable managers to review, approve, or reject nominations — from the portal or directly from email.",
                "stories": [
                    (
                        "View pending approvals",
                        "As a manager, I can view all pending nominations assigned to me, "
                        "including nominator, beneficiary, amount, description, and fraud risk "
                        "level, so that I can make informed approval decisions.",
                    ),
                    (
                        "Approve or reject a nomination",
                        "As a manager, I can approve or reject a pending nomination with a "
                        "single action, so that approved awards proceed to payroll and rejected "
                        "awards are closed.",
                    ),
                    (
                        "One-click email approval",
                        "As a manager, I can approve or reject a nomination via a secure link "
                        "in my notification email without logging in to the portal, so that "
                        "approvals can be handled quickly from any device.",
                    ),
                ],
            },
            {
                "feature": "Nomination History",
                "description": "Provide full visibility into past nomination activity for all users.",
                "stories": [
                    (
                        "View nomination history",
                        "As a user, I can view my full nomination history — sent, received, "
                        "and approved — so that I have complete visibility into my award activity.",
                    ),
                ],
            },
            {
                "feature": "Payroll Integration",
                "description": "Generate payroll-ready extracts of approved nominations for finance processing.",
                "stories": [
                    (
                        "Generate payroll extract",
                        "As an admin, I can generate a payroll extract CSV for approved "
                        "nominations so that finance can process award payments.",
                    ),
                ],
            },
        ],
    },
    # ── 2 ─────────────────────────────────────────────────────────────────────
    {
        "epic": "Identity & Authentication",
        "description": (
            "Microsoft Entra ID (Azure AD) OAuth2 / MSAL authentication, JWT validation "
            "on every API request, and role-based access control (RBAC)."
        ),
        "features": [
            {
                "feature": "Azure AD Sign-In",
                "description": "Authenticate users via Microsoft Entra ID with MSAL OAuth2.",
                "stories": [
                    (
                        "Sign in with Microsoft work account",
                        "As a user, I can sign in using my Microsoft Azure AD work account "
                        "via MSAL OAuth2, so that I do not need a separate username and password.",
                    ),
                    (
                        "Unauthenticated users see sign-in prompt",
                        "As an unauthenticated visitor, I am shown a sign-in screen so that "
                        "no application data is exposed without a valid session.",
                    ),
                ],
            },
            {
                "feature": "API Security",
                "description": "Validate every API request with a signed JWT from Microsoft Entra ID.",
                "stories": [
                    (
                        "JWT validation on every API request",
                        "As a system, every API request validates the Bearer JWT against "
                        "Microsoft Entra ID, so that unauthenticated or tampered requests "
                        "are rejected with 401.",
                    ),
                ],
            },
            {
                "feature": "Role-Based Access Control",
                "description": "Gate admin-only features behind an Azure AD app role.",
                "stories": [
                    (
                        "Role-based access for admin users",
                        "As an admin holding the AWard_Nomination_Admin app role, I have "
                        "access to admin-only features (fraud stats, audit logs, impersonation, "
                        "analytics) that are hidden from regular users.",
                    ),
                ],
            },
        ],
    },
    # ── 3 ─────────────────────────────────────────────────────────────────────
    {
        "epic": "Multi-Tenant SaaS Architecture",
        "description": (
            "Per-tenant data isolation, custom branding (theme, locale, currency), "
            "dedicated domain support, and domain-mismatch error handling."
        ),
        "features": [
            {
                "feature": "Data Isolation",
                "description": "Strictly separate each tenant's data and map Azure AD tenants to internal tenants.",
                "stories": [
                    (
                        "Per-tenant data isolation",
                        "As a tenant, all my data — users, nominations, fraud scores — is "
                        "strictly isolated from other tenants via TenantId filtering on every "
                        "DB query.",
                    ),
                    (
                        "Azure AD tenant ID maps to internal tenant",
                        "As a system, each organisation's Azure AD tenant GUID (tid JWT claim) "
                        "is linked to an internal Tenant record, so that authentication "
                        "automatically resolves which organisation a user belongs to.",
                    ),
                ],
            },
            {
                "feature": "Tenant Branding",
                "description": "Allow each tenant to configure their own visual theme, language, and currency.",
                "stories": [
                    (
                        "Custom colour theme per tenant",
                        "As a tenant administrator, I can configure a custom primary colour "
                        "theme stored in the Tenants Config JSON, so that my portal matches "
                        "our brand.",
                    ),
                    (
                        "Custom locale and currency per tenant",
                        "As a tenant administrator, I can set the portal language (e.g. en-US, "
                        "ko-KR) and currency (e.g. USD, KRW) so that UI and amounts are "
                        "localised.",
                    ),
                ],
            },
            {
                "feature": "Tenant Domain Management",
                "description": "Assign dedicated hostnames per tenant and handle users who land on the wrong portal.",
                "stories": [
                    (
                        "Dedicated custom domain per tenant",
                        "As a tenant administrator, I can assign a dedicated public hostname "
                        "(e.g. acme-awards.terian-services.com) so that users access a "
                        "branded URL.",
                    ),
                    (
                        "Wrong-portal error screen",
                        "As a user on the wrong tenant portal, I see a clear error page with "
                        "a direct link to my correct portal, so that I can navigate there "
                        "without IT help.",
                    ),
                ],
            },
        ],
    },
    # ── 4 ─────────────────────────────────────────────────────────────────────
    {
        "epic": "Fraud Detection System",
        "description": (
            "Two-layer fraud detection: rule-based SQL checks plus a trained Random Forest "
            "ML classifier. Automatic blocking of critical-risk nominations, admin visibility, "
            "and on-demand model retraining."
        ),
        "features": [
            {
                "feature": "Fraud Scoring Engine",
                "description": "Score every nomination at creation time and block critical-risk items automatically.",
                "stories": [
                    (
                        "Automatic fraud scoring on nomination creation",
                        "As a system, every new nomination is automatically assessed for fraud "
                        "risk, computing a score (0-100) and risk level "
                        "(NONE/LOW/MEDIUM/HIGH/CRITICAL) using 20+ engineered features.",
                    ),
                    (
                        "Block CRITICAL-risk nominations automatically",
                        "As a system, nominations scoring CRITICAL (70-100) are automatically "
                        "blocked and not persisted, so that high-confidence fraud is stopped "
                        "before approval.",
                    ),
                ],
            },
            {
                "feature": "Rule-Based Detection",
                "description": "Detect common fraud patterns using deterministic SQL rules.",
                "stories": [
                    (
                        "Rule-based SQL fraud checks",
                        "As a system, fraud detection includes SQL rule checks for high "
                        "nomination frequency, repeated beneficiaries, circular schemes, "
                        "unusual amounts, and rapid approvals.",
                    ),
                ],
            },
            {
                "feature": "ML Fraud Classifier",
                "description": "Detect subtle fraud patterns with a trained Random Forest model and support on-demand retraining.",
                "stories": [
                    (
                        "ML fraud classifier — Random Forest",
                        "As a system, fraud detection includes a trained Random Forest "
                        "classifier analysing user behaviour, temporal signals, relationship "
                        "graphs, and amount anomalies, catching subtle fraud missed by rules.",
                    ),
                    (
                        "On-demand model retraining and metadata",
                        "As an admin, I can trigger on-demand ML model retraining and view "
                        "model metadata — training date, sample count, AUC, feature importance.",
                    ),
                ],
            },
            {
                "feature": "Fraud Reporting",
                "description": "Give admins live visibility into fraud scores and flagged nominations.",
                "stories": [
                    (
                        "Admin fraud statistics view",
                        "As an admin, I can view live fraud statistics including score "
                        "distributions, risk breakdowns, and flagged nominations.",
                    ),
                ],
            },
        ],
    },
    # ── 5 ─────────────────────────────────────────────────────────────────────
    {
        "epic": "Admin & Governance",
        "description": (
            "Admin user impersonation with full audit logging, impersonation audit log viewer, "
            "and region diagnostic endpoint."
        ),
        "features": [
            {
                "feature": "User Impersonation",
                "description": "Allow admins to act on behalf of any user for troubleshooting.",
                "stories": [
                    (
                        "Admin user impersonation",
                        "As an admin, I can impersonate any user via the Admin Impersonation "
                        "Panel to troubleshoot issues or perform actions on their behalf.",
                    ),
                ],
            },
            {
                "feature": "Audit & Compliance",
                "description": "Log all impersonation actions and expose an audit trail for compliance review.",
                "stories": [
                    (
                        "Full impersonation audit logging",
                        "As a system, every admin impersonation action is logged to "
                        "Impersonation_AuditLog with timestamp, admin UPN, impersonated UPN, "
                        "action, and client IP.",
                    ),
                    (
                        "View impersonation audit log",
                        "As an admin, I can view the full impersonation audit log in the "
                        "dashboard for compliance and security review.",
                    ),
                ],
            },
            {
                "feature": "System Diagnostics",
                "description": "Expose runtime information to help diagnose routing and region issues.",
                "stories": [
                    (
                        "Region diagnostic endpoint",
                        "As an admin, I can call /whoami to see which Azure region and "
                        "Container App revision is serving my request to diagnose routing issues.",
                    ),
                ],
            },
        ],
    },
    # ── 6 ─────────────────────────────────────────────────────────────────────
    {
        "epic": "Analytics & AI Agent",
        "description": (
            "Admin analytics dashboard with spending trends, department breakdowns, and "
            "diversity metrics; OpenAI-powered analytics agent with current-user awareness, "
            "SQL tools, fraud model tool, and multi-format export."
        ),
        "features": [
            {
                "feature": "Analytics Dashboard",
                "description": "Provide admins with a rich visual dashboard covering spend, fraud, approvals, and diversity.",
                "stories": [
                    (
                        "Analytics dashboard",
                        "As an admin, I can view a dashboard with overview metrics, spending "
                        "trends, department breakdowns, and top nominators and recipients.",
                    ),
                    (
                        "Fraud analytics in the dashboard",
                        "As an admin, I can view fraud analytics including high-risk alerts "
                        "and fraud score trends over time.",
                    ),
                    (
                        "Approval metrics and diversity statistics",
                        "As an admin, I can view approval time metrics and nomination diversity "
                        "statistics to identify bottlenecks and monitor programme inclusivity.",
                    ),
                ],
            },
            {
                "feature": "AI Analytics Agent",
                "description": "Answer natural language questions about nominations using an OpenAI-powered agent with SQL and fraud model tools.",
                "stories": [
                    (
                        "Natural language AI analytics agent",
                        "As an admin, I can ask natural language questions about nomination "
                        "data (e.g. 'which department spent most last quarter?') and receive "
                        "answers from an OpenAI-powered agent that queries the database.",
                    ),
                    (
                        "Current-user context in the AI agent",
                        "As an admin, pronouns like 'I', 'me', and 'my' in my questions are "
                        "automatically resolved to my identity, so I can ask 'what is my fraud "
                        "score?' and get accurate answers.",
                    ),
                    (
                        "Fraud model as an AI agent tool",
                        "As an admin, the AI analytics agent can call a fraud model tool to "
                        "answer questions about model performance, training data, and feature "
                        "importance.",
                    ),
                ],
            },
            {
                "feature": "Analytics Export",
                "description": "Export analytics results in multiple formats for reporting and downstream systems.",
                "stories": [
                    (
                        "Multi-format analytics export",
                        "As an admin, I can export analytics results to CSV, Excel, PDF, or "
                        "Azure Blob Storage formats to share reports or feed data into other "
                        "systems.",
                    ),
                ],
            },
        ],
    },
    # ── 7 ─────────────────────────────────────────────────────────────────────
    {
        "epic": "Email Notifications",
        "description": (
            "SendGrid-powered manager notifications on nomination creation, with secure "
            "action-token links for one-click approval or rejection from email."
        ),
        "features": [
            {
                "feature": "Manager Notifications",
                "description": "Notify managers by email when nominations require their approval, including full details and action buttons.",
                "stories": [
                    (
                        "Manager receives approval-request email",
                        "As a manager, I receive an email when a new nomination needing my "
                        "approval is submitted, so that I am promptly informed.",
                    ),
                    (
                        "Nomination details and action buttons in email",
                        "As a manager, the email contains full nomination details — nominator, "
                        "beneficiary, amount, description — plus approve and reject buttons.",
                    ),
                ],
            },
            {
                "feature": "Email Security",
                "description": "Protect email action links with cryptographically signed single-use tokens.",
                "stories": [
                    (
                        "Secure single-use action tokens",
                        "As a system, email action links use cryptographically signed, "
                        "single-use tokens that expire after use, preventing replay attacks.",
                    ),
                ],
            },
        ],
    },
    # ── 8 ─────────────────────────────────────────────────────────────────────
    {
        "epic": "Infrastructure & DevOps",
        "description": (
            "GitHub Actions CI/CD pipelines, Azure Front Door global routing and failover, "
            "auto-scaling Container Apps, and Log Analytics / Kusto observability."
        ),
        "features": [
            {
                "feature": "CI/CD Pipelines",
                "description": "Automate build and deployment of backend and frontend on every push to main.",
                "stories": [
                    (
                        "Backend CI/CD to dual-region Container Apps",
                        "As a developer, pushing to main auto-builds a Docker image, tags it "
                        "with the git SHA, pushes to Azure Container Registry, and deploys to "
                        "both East US and West US Container Apps.",
                    ),
                    (
                        "Frontend CI/CD to Azure Static Web Apps",
                        "As a developer, pushing frontend changes to main automatically builds "
                        "and deploys the React SPA to Azure Static Web Apps.",
                    ),
                ],
            },
            {
                "feature": "Global Routing & Scaling",
                "description": "Route traffic globally via Front Door with automatic failover and HTTP-based auto-scaling.",
                "stories": [
                    (
                        "Azure Front Door global routing and failover",
                        "As a system, Azure Front Door routes /api/* traffic across two regions "
                        "with automatic failover on consecutive health probe failures.",
                    ),
                    (
                        "Container App auto-scaling (0-10 replicas)",
                        "As a system, each Container App region auto-scales 0-10 replicas "
                        "based on HTTP concurrency, handling traffic spikes at zero idle cost.",
                    ),
                ],
            },
            {
                "feature": "Observability",
                "description": "Centralise logs in Log Analytics and provide pre-built Kusto queries for key operational signals.",
                "stories": [
                    (
                        "Log Analytics and Kusto observability",
                        "As an ops engineer, I can run pre-built Kusto queries to investigate "
                        "fraud trends, email failures, error spikes, and region performance.",
                    ),
                ],
            },
        ],
    },
    # ── 9 ─────────────────────────────────────────────────────────────────────
    {
        "epic": "MCP Server Integration",
        "description": (
            "Model Context Protocol (MCP) servers exposing nomination data and analytics "
            "export capabilities to AI agents and developer tooling."
        ),
        "features": [
            {
                "feature": "MCP Servers",
                "description": "Expose nomination data and export capabilities as MCP servers for AI tooling.",
                "stories": [
                    (
                        "Nominations SQL Agent MCP server",
                        "As a developer, I can connect a Nominations SQL Agent MCP server to "
                        "my AI tooling to query nomination data using natural language tools.",
                    ),
                    (
                        "Analytics Export Service MCP server",
                        "As a developer, I can connect an Analytics Export MCP server that "
                        "exports data to CSV, Excel, PDF, or Azure Blob Storage.",
                    ),
                ],
            },
            {
                "feature": "AI Tool Registry",
                "description": "Dispatch registered tool functions from the in-app AI agent based on the question asked.",
                "stories": [
                    (
                        "AI analytics agent tool registry",
                        "As an admin, the in-app AI analytics agent dispatches registered tool "
                        "functions (SQL query, spending analysis, fraud model info) based on "
                        "the question asked, enabling accurate multi-step analytical answers.",
                    ),
                ],
            },
        ],
    },
    # ── 10 ────────────────────────────────────────────────────────────────────
    {
        "epic": "Infrastructure as Code (Terraform)",
        "description": (
            "Full Azure infrastructure defined in Terraform with reusable modules, "
            "multi-environment support (dev/sandbox/prod), private networking via VNet "
            "injection and private endpoints, remote state, and Azure Managed Grafana "
            "observability. Covers both implemented capabilities and future hardening."
        ),
        "features": [
            {
                "feature": "Terraform Foundation",
                "description": "Modular, multi-environment Terraform structure with remote state and multi-phase deployment scripts.",
                "stories": [
                    (
                        "Modular Terraform architecture",
                        "As an ops engineer, the infrastructure is split into 11 reusable "
                        "Terraform modules (networking, sql, storage, container-registry, "
                        "key-vault, openai, log-analytics, container-apps, front-door, "
                        "static-web-app, grafana) so that each concern is independently "
                        "versioned and composable across environments.",
                    ),
                    (
                        "Multi-environment structure (dev / sandbox / prod)",
                        "As an ops engineer, each environment (dev, sandbox, prod) has its "
                        "own Terraform workspace under environments/ with dedicated tfvars and "
                        "isolated state, so that changes can be tested in dev before promoting "
                        "to production.",
                    ),
                    (
                        "Remote state in Azure Blob Storage",
                        "As an ops engineer, Terraform state for every environment is stored "
                        "in Azure Blob Storage (awardnomplatform / tfstate) with per-environment "
                        "keys, so that state is shared across the team and never committed to "
                        "source control.",
                    ),
                    (
                        "Bootstrap and multi-phase deployment scripts",
                        "As an ops engineer, pre-terraform, mid-terraform, and post-terraform "
                        "PowerShell scripts handle steps that cannot be expressed in HCL alone "
                        "(e.g. SQL schema deployment, ACR image push, Container App image "
                        "update) so that a full environment can be stood up from zero with "
                        "documented steps.",
                    ),
                ],
            },
            {
                "feature": "Private Networking",
                "description": "VNet-inject Container Apps and lock all PaaS services behind private endpoints with private DNS.",
                "stories": [
                    (
                        "Private networking — VNets and ACA subnet injection",
                        "As an ops engineer, each environment has a primary (East US) and "
                        "secondary (West US) VNet with dedicated ACA subnets, bidirectional "
                        "VNet peering, and Container App Environments injected into those "
                        "subnets, so that backend traffic never traverses the public internet.",
                    ),
                    (
                        "Private endpoints for SQL, Storage, Key Vault, ACR, and OpenAI",
                        "As an ops engineer, each managed service (SQL Server, Blob Storage, "
                        "Key Vault, Container Registry, OpenAI) has a private endpoint in the "
                        "subnet-privatelinks subnet, so that all data-plane traffic stays on "
                        "the Azure backbone and public network access can be disabled.",
                    ),
                    (
                        "Private DNS zones for all private-link resources",
                        "As an ops engineer, five Private DNS zones (SQL, blob, Key Vault, "
                        "ACR, OpenAI) are provisioned and linked to both primary and secondary "
                        "VNets, so that private endpoint FQDNs resolve correctly from within "
                        "the VNet without manual host-file entries.",
                    ),
                ],
            },
            {
                "feature": "Identity & Secrets Management",
                "description": "Manage Managed Identities, Key Vault secrets, and Azure AD app registrations entirely through Terraform.",
                "stories": [
                    (
                        "User-assigned Managed Identities for Container Apps",
                        "As an ops engineer, each Container App region uses a pre-created "
                        "user-assigned managed identity (not system-assigned) so that Key Vault "
                        "access policies are granted before the Container App is created, "
                        "eliminating the race condition that caused 5-second timeout errors.",
                    ),
                    (
                        "Key Vault secrets wired from module outputs",
                        "As an ops engineer, Key Vault secrets for SQL connection strings, "
                        "storage keys, and OpenAI credentials are derived from live module "
                        "outputs (not hardcoded in tfvars) so that secrets stay in sync "
                        "automatically whenever infrastructure is reprovisioned.",
                    ),
                    (
                        "Azure AD App Registrations managed by Terraform",
                        "As an ops engineer, Entra ID app registrations (API + frontend) for "
                        "the dev and sandbox environments are created and managed by Terraform "
                        "using the azuread provider, so that client IDs, redirect URIs, app "
                        "roles, and API scopes are version-controlled and reproducible.",
                    ),
                ],
            },
            {
                "feature": "Monitoring & Observability",
                "description": "Provision Grafana and Azure Monitor alerts through Terraform for pre-wired observability.",
                "stories": [
                    (
                        "Azure Managed Grafana connected to Log Analytics",
                        "As an ops engineer, an Azure Managed Grafana instance (Standard SKU) "
                        "is provisioned with system-assigned identity and Monitoring Reader "
                        "role on both Log Analytics workspaces, so that I have a pre-wired "
                        "observability dashboard without manual data-source configuration.",
                    ),
                    (
                        "Azure Monitor alert rules for key signals",
                        "As an ops engineer, I want Terraform to provision Azure Monitor alert "
                        "rules for fraud-spike rate, HTTP 5xx error rate, high API latency, "
                        "and Container App scale-to-zero events, so that the on-call team is "
                        "notified automatically without manually configuring alerts in the portal.",
                    ),
                ],
            },
            {
                "feature": "Security Hardening",
                "description": "Upgrade Front Door to Premium SKU to enable WAF and private-link ingress.",
                "stories": [
                    (
                        "Upgrade Front Door to Premium for WAF and private link",
                        "As an ops engineer, I want to upgrade Front Door Standard to Premium "
                        "SKU so that the Web Application Firewall (OWASP ruleset) is enabled "
                        "and Container Apps can switch to internal load balancer mode with "
                        "Front Door Private Link, removing all public inbound access to the "
                        "backends.",
                    ),
                ],
            },
        ],
    },
]

# ── Auth helpers ───────────────────────────────────────────────────────────────

def get_headers() -> dict:
    # 1 — PAT env var
    pat = os.environ.get("ADO_PAT", "").strip()
    if pat:
        print("Auth: ADO_PAT env var")
        tok = base64.b64encode(f":{pat}".encode()).decode()
        return {"Authorization": f"Basic {tok}",
                "Content-Type": "application/json-patch+json",
                "Accept": "application/json"}

    # 2 — Azure CLI session
    try:
        r = subprocess.run(
            ["az", "account", "get-access-token",
             "--resource", "499b84ac-1321-427f-aa17-267ca6975798",
             "--output", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            bearer = json.loads(r.stdout).get("accessToken", "")
            if bearer:
                print("Auth: Azure CLI session")
                return {"Authorization": f"Bearer {bearer}",
                        "Content-Type": "application/json-patch+json",
                        "Accept": "application/json"}
    except Exception:
        pass

    # 3 — Interactive PAT
    print("\nNo ADO_PAT env var and Azure CLI token not found.")
    print("Create a PAT at: https://dev.azure.com/Terian-Services/_usersSettings/tokens")
    print("Required scope: Work Items (Read, Write, Manage)\n")
    pat = input("Paste your PAT: ").strip()
    if not pat:
        sys.exit("No PAT provided. Exiting.")
    tok = base64.b64encode(f":{pat}".encode()).decode()
    return {"Authorization": f"Basic {tok}",
            "Content-Type": "application/json-patch+json",
            "Accept": "application/json"}


def fetch_existing_items(item_type: str, hdrs: dict) -> dict:
    """Return {lower(title): id} for all existing work items of item_type."""
    url  = f"{BASE_URL}/wit/wiql?api-version={API_VER}"
    wiql_hdrs = dict(hdrs)
    wiql_hdrs["Content-Type"] = "application/json"
    query = (
        f"SELECT [System.Id],[System.Title] FROM WorkItems "
        f"WHERE [System.TeamProject] = '{PROJECT}' "
        f"AND [System.WorkItemType] = '{item_type}' "
        f"AND [System.State] <> 'Removed'"
    )
    body = json.dumps({"query": query}).encode()
    req  = urllib.request.Request(url, data=body, headers=wiql_hdrs, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  ⚠ WIQL query failed ({e.code}): {e.read().decode()[:200]}")
        return {}

    ids = [wi["id"] for wi in result.get("workItems", [])]
    if not ids:
        return {}

    # Batch-fetch titles + IDs (up to 200 per request)
    items: dict = {}
    batch_size = 200
    get_hdrs = {k: v for k, v in hdrs.items() if k != "Content-Type"}
    get_hdrs["Accept"] = "application/json"
    for i in range(0, len(ids), batch_size):
        batch     = ids[i : i + batch_size]
        ids_param = ",".join(str(x) for x in batch)
        detail_url = (
            f"https://dev.azure.com/{ORG}/_apis/wit/workitems"
            f"?ids={ids_param}&fields=System.Id,System.Title&api-version={API_VER}"
        )
        req2 = urllib.request.Request(detail_url, headers=get_hdrs)
        try:
            with urllib.request.urlopen(req2) as r2:
                data = json.loads(r2.read())
                for wi in data.get("value", []):
                    t  = wi.get("fields", {}).get("System.Title", "")
                    wid = wi.get("id")
                    if t and wid:
                        items[t.strip().lower()] = wid
        except urllib.error.HTTPError as e:
            print(f"  ⚠ Batch fetch failed ({e.code}): {e.read().decode()[:200]}")
    return items


def detect_story_type(hdrs: dict) -> str:
    """
    Query the project's work item types and return the best match for a
    story-level backlog item.  Preference order:
      User Story → Product Backlog Item → Story → Requirement → first non-Epic type
    """
    url = f"{BASE_URL}/wit/workitemtypes?api-version={API_VER}"
    get_hdrs = {k: v for k, v in hdrs.items() if k != "Content-Type"}
    get_hdrs["Accept"] = "application/json"
    req = urllib.request.Request(url, headers=get_hdrs)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  ⚠ Could not fetch work item types ({e.code}): {e.read().decode()[:200]}")
        return "User Story"   # fall back and let the API error surface

    names = [t["name"] for t in data.get("value", [])]
    print(f"  Available work item types: {names}")

    preferred = ["User Story", "Product Backlog Item", "Story", "Requirement"]
    for candidate in preferred:
        if candidate in names:
            return candidate

    # Fall back to the first type that isn't Epic or Feature
    skip = {"Epic", "Feature", "Bug", "Task", "Test Case", "Impediment", "Issue"}
    for name in names:
        if name not in skip:
            return name

    return names[0] if names else "User Story"


def create_item(item_type: str, fields: dict, hdrs: dict) -> dict:
    item_type_encoded = item_type.replace(" ", "%20")
    url  = f"{BASE_URL}/wit/workitems/${item_type_encoded}?api-version={API_VER}"
    body = [{"op": "add", "path": f"/fields/{k}", "value": v} for k, v in fields.items()]
    req  = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=hdrs, method="PATCH")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"    ✗ HTTP {e.code}: {e.read().decode()[:300]}")
        return {}


def link_parent(child_id: int, parent_id: int, hdrs: dict) -> None:
    url  = (f"https://dev.azure.com/{ORG}/_apis/wit/workitems/{child_id}"
            f"?api-version={API_VER}")
    body = [{"op": "add", "path": "/relations/-", "value": {
        "rel": "System.LinkTypes.Hierarchy-Reverse",
        "url": f"https://dev.azure.com/{ORG}/_apis/wit/workitems/{parent_id}",
    }}]
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=hdrs, method="PATCH")
    try:
        with urllib.request.urlopen(req): pass
    except urllib.error.HTTPError as e:
        print(f"    Link err {e.code}: {e.read().decode()[:200]}")


def main():
    hdrs = get_headers()
    n_epics    = len(BACKLOG)
    n_features = sum(len(e["features"]) for e in BACKLOG)
    n_stories  = sum(len(f["stories"]) for e in BACKLOG for f in e["features"])
    print(f"\nProject : {PROJECT}  |  Org: {ORG}")
    print(f"Backlog : {n_epics} epics  →  {n_features} features  →  {n_stories} user stories")
    print("=" * 60)

    # ── Detect the correct story-level work item type ───────────────────────────
    print("\nDetecting work item types…")
    story_type = detect_story_type(hdrs)
    print(f"  Using story type: '{story_type}'")

    # ── Pre-flight: load all existing items (title → id) ───────────────────────
    print("\nChecking for existing work items (duplicate prevention)…")
    existing_epics    = fetch_existing_items("Epic",    hdrs)
    existing_features = fetch_existing_items("Feature", hdrs)
    existing_stories  = fetch_existing_items(story_type, hdrs)
    print(f"  Found {len(existing_epics)} Epic(s), {len(existing_features)} Feature(s), "
          f"{len(existing_stories)} {story_type}(s).")

    ok_e, skip_e = 0, 0
    ok_f, skip_f = 0, 0
    ok_s, skip_s = 0, 0

    for entry in BACKLOG:
        epic_title = entry["epic"]
        epic_key   = epic_title.strip().lower()
        print(f"\n▶ EPIC: {epic_title}")

        # ── Epic: create or reuse ───────────────────────────────────────────────
        if epic_key in existing_epics:
            eid = existing_epics[epic_key]
            print(f"  ↷ Already exists — reusing #{eid}")
            skip_e += 1
        else:
            r = create_item("Epic", {
                "System.Title":       epic_title,
                "System.Description": entry["description"],
            }, hdrs)
            eid = r.get("id")
            if not eid:
                print("  ✗ Epic failed — skipping"); continue
            print(f"  ✓ Epic #{eid}")
            ok_e += 1
            existing_epics[epic_key] = eid
            time.sleep(0.3)

        for feat in entry["features"]:
            feat_title = feat["feature"]
            feat_key   = feat_title.strip().lower()
            print(f"\n  ▶ FEATURE: {feat_title}")

            # ── Feature: create or reuse ────────────────────────────────────────
            if feat_key in existing_features:
                fid = existing_features[feat_key]
                print(f"    ↷ Already exists — reusing #{fid}")
                skip_f += 1
            else:
                r = create_item("Feature", {
                    "System.Title":       feat_title,
                    "System.Description": feat["description"],
                }, hdrs)
                fid = r.get("id")
                if not fid:
                    print("    ✗ Feature failed — skipping stories"); continue
                link_parent(fid, eid, hdrs)
                print(f"    ✓ Feature #{fid}")
                ok_f += 1
                existing_features[feat_key] = fid
                time.sleep(0.3)

            # ── Stories: create missing ones, linked to feature ─────────────────
            for title, desc in feat["stories"]:
                story_key = title.strip().lower()
                if story_key in existing_stories:
                    print(f"      ↷ Already exists — skipped: {title}")
                    skip_s += 1
                    continue

                sr  = create_item(story_type, {
                    "System.Title":       title,
                    "System.Description": desc,
                }, hdrs)
                sid = sr.get("id")
                if not sid:
                    print(f"      ✗ Story failed: {title}"); continue
                link_parent(sid, fid, hdrs)
                print(f"      ✓ #{sid}: {title}")
                ok_s += 1
                existing_stories[story_key] = sid
                time.sleep(0.3)

    print(f"\n{'='*60}")
    print(f"Created — Epics: {ok_e}  Features: {ok_f}  Stories: {ok_s}")
    print(f"Skipped — Epics: {skip_e}  Features: {skip_f}  Stories: {skip_s} (already existed)")
    print(f"\nBacklog: https://dev.azure.com/{ORG}/{PROJECT}/_backlogs/backlog")


if __name__ == "__main__":
    main()
