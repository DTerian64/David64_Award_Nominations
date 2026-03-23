# Award Nomination Application — Architecture Documentation

## System Overview

The Award Nomination Application is a cloud-native, globally distributed, **multi-tenant SaaS** web application built on Microsoft Azure. It enables employees across multiple organisations (tenants) to nominate colleagues for monetary awards, with built-in ML fraud detection, an OpenAI-powered analytics agent, and a fully private Azure network topology where **Azure Front Door is the sole public API ingress** — all backend services communicate exclusively over the Azure backbone via private endpoints.

The entire infrastructure is defined in **Terraform** (modular, multi-environment: dev / sandbox / prod) with remote state stored in Azure Blob Storage.

---

## Architecture Principles

| Principle | Implementation |
|-----------|----------------|
| **Zero public inbound to backends** | Container Apps are VNet-injected; all PaaS reachable only via private endpoints |
| **Single public API ingress** | Azure Front Door — WAF, SSL termination, global load balancing |
| **Secrets never in code** | All secrets in Azure Key Vault; injected at runtime via Managed Identity |
| **Per-tenant isolation** | Data filtered by `TenantId` on every query; per-tenant domains, themes, locale |
| **Infrastructure as code** | 100% Terraform — reproducible across dev, sandbox, prod |
| **Active-active multi-region** | East US + West US Container Apps behind Front Door 50/50 weighted |

---

## Architecture Components

### 1. Multi-Tenant SaaS Layer

Each organisation (tenant) is provisioned with:

- A dedicated subdomain: `acme-awards.terian-services.com`, `sandbox-awards.terian-services.com`, `dev-awards.terian-services.com`
- A CNAME DNS record pointing to the Azure Front Door endpoint, managed in the Azure DNS Zone by Terraform
- A row in the `dbo.Tenants` SQL table containing: `TenantId`, `Domain`, `AzureAdTenantId`, and a `Config` JSON blob (primary colour, locale, currency, logo URL)
- An Azure AD tenant GUID mapped to the internal tenant record via the `tid` JWT claim

**Domain mismatch handling:** If a user authenticates against the wrong tenant portal, the frontend renders a blocking "Wrong portal" error screen with a direct link to the correct domain. No automatic cross-domain redirects are used (avoids MSAL session loops).

**Per-tenant customisation** (stored in `Tenants.Config`):
- Primary colour theme
- Language / locale (e.g. `en-US`, `ko-KR`)
- Currency (e.g. `USD`, `KRW`)

---

### 2. Frontend Layer

**Service:** Azure Static Web Apps
**Technology:** React SPA (TypeScript, Vite)
**Deployment:** GitHub Actions CI/CD

- Served from Azure edge locations globally (CDN-backed)
- Authenticates via MSAL (`@azure/msal-browser`) using the OAuth2 authorization code flow
- Fetches tenant configuration (theme, locale, currency, canonical domain) from `/api/tenant/config` on startup
- Per-tenant custom domains configured via `azurerm_static_web_app_custom_domain` (Terraform)

---

### 3. API Gateway & Public Ingress

**Service:** Azure Front Door Standard
**Role:** The **sole public ingress point** for all API traffic

| Capability | Detail |
|------------|--------|
| SSL/TLS termination | HTTPS only; certificates managed by Front Door |
| WAF | OWASP-based rule set (Standard SKU; Premium planned for private-link ingress) |
| Global load balancing | 50 / 50 weighted split between East US and West US origins |
| Health probes | HEAD `/` every 100 s; automatic failover on 3 consecutive failures |
| DDoS protection | Inherited from Azure edge network |

**Routing:**
- `/api/*` → Backend Container Apps (East US primary, West US failover)
- Static assets → Azure Static Web Apps

> **Note:** Container Apps currently use `internal_load_balancer_enabled = false` (public IP, reachable by Front Door Standard). Upgrading to Front Door **Premium** SKU will enable Private Link ingress so Container Apps can move to internal-only load balancers, removing the last public inbound path.

---

### 4. Private Network (VNet Architecture)

All backend-to-backend and backend-to-data traffic is carried entirely on the **Azure backbone** — no traffic transits the public internet after entering Front Door.

```
VNet Primary  — East US   10.2.0.0/16
  ├─ subnet-aca-primary        10.2.1.0/24   Container App Environment (VNet-injected)
  └─ subnet-privatelinks-primary 10.2.2.0/24 Private endpoints: SQL, KV, Blob, ACR, OpenAI

VNet Secondary — West US  10.3.0.0/16
  ├─ subnet-aca-secondary      10.3.1.0/24   Container App Environment (VNet-injected)
  └─ (private endpoints resolve via VNet peering to primary endpoints)

VNet Peering: bidirectional, allow-forwarded-traffic
```

**Private DNS Zones** (linked to both VNets):

| Zone | Resource |
|------|----------|
| `privatelink.database.windows.net` | Azure SQL |
| `privatelink.blob.core.windows.net` | Blob Storage |
| `privatelink.vaultcore.azure.net` | Key Vault |
| `privatelink.azurecr.io` | Container Registry |
| `privatelink.openai.azure.com` | Azure OpenAI |

Private DNS zones ensure that FQDN lookups from within the VNet resolve to private IP addresses — no host-file entries required.

---

### 5. Application Backend

**Service:** Azure Container Apps
**Environments:**
- `award-api-eastus` (Primary — East US)
- `award-api-westus` (Secondary — West US)

**Technology Stack:**
- Python 3.11 · FastAPI · Uvicorn ASGI
- Containerised (Docker) · images in Azure Container Registry

**Networking:** Both Container App Environments are VNet-injected (`infrastructure_subnet_id = subnet-aca-primary/secondary`). Service endpoints on the ACA subnets allow direct access to Key Vault and Blob Storage.

**Identity:** User-assigned Managed Identities (`id-award-api-primary-{env}`, `id-award-api-secondary-{env}`) are created **before** the Container Apps and granted Key Vault access policies ahead of time — this eliminates the race condition where Azure would try to resolve KV-backed secrets before the system-assigned identity access policy existed.

**Key Vault secret injection:** Secrets are referenced by name at runtime via the Container Apps KV secret reference mechanism — the actual secret values never appear in Terraform state or environment configuration files.

**Key API Endpoints:**

```
GET  /                          Health check
GET  /health                    Health status
GET  /docs                      Swagger UI
GET  /whoami                    Region diagnostic (admin only)
GET  /api/tenant/config         Tenant configuration (theme, locale, domain)
GET  /api/users                 User directory
POST /api/nominations/create    Create nomination (triggers fraud scoring)
GET  /api/nominations/pending   Pending approvals for current manager
POST /api/nominations/approve   Approve / reject nomination
GET  /api/nominations/history   Full nomination history
GET  /api/admin/audit-logs      Impersonation audit log (admin only)
GET  /api/admin/fraud-stats     Fraud statistics (admin only)
POST /api/admin/retrain         Trigger ML model retraining (admin only)
GET  /api/analytics/*           Analytics dashboard data (admin only)
POST /api/analytics/agent       AI analytics agent query (admin only)
```

---

### 6. Azure Key Vault

**Service:** Azure Key Vault
**Access:** Private endpoint only (`privatelink.vaultcore.azure.net`)
**Auth:** RBAC (`enableRbacAuthorization: true`) — no legacy access policies for data plane
**Soft delete:** 90 days

Secrets managed by Terraform (values derived from module outputs — never hardcoded):

| Secret Name | Value Source |
|-------------|-------------|
| `SQL-SERVER` | `module.sql.server_fqdn` |
| `SQL-DATABASE` | `module.sql.database_name` |
| `SQL-USER` | `terraform.tfvars` |
| `SQL-PASSWORD` | `terraform.tfvars` |
| `AZURE-STORAGE-KEY` | `module.storage.primary_access_key` |
| `AZURE-OPENAI-KEY` | `module.openai.primary_access_key` |
| `AZURE-OPENAI-ENDPOINT` | `module.openai.endpoint` |
| `GMAIL-APP-PASSWORD` | `terraform.tfvars` |

---

### 7. Database Layer

**Service:** Azure SQL Database
**Server:** `{sql-server-name}.database.windows.net`
**Access:** Private endpoint only — no public network access after hardening
**Version:** SQL Server 12.0 · TLS 1.2 minimum · Serverless Gen5 autoscale

**Tables:**

| Table | Purpose |
|-------|---------|
| `Tenants` | Organisation registry: domain, AAD tenant ID, Config JSON |
| `Users` | Employee directory with manager hierarchy (per-tenant) |
| `Nominations` | Award records with status tracking (per-tenant) |
| `FraudScores` | ML fraud assessment per nomination |
| `FraudAnalytics` | Aggregated fraud patterns and metrics |
| `Impersonation_AuditLog` | Admin impersonation audit trail with IP |

**Authentication:** SQL username/password (dev/sandbox); Managed Identity SQL auth is the target for production.

---

### 8. ML Model Storage

**Service:** Azure Blob Storage
**Access:** Private endpoint only
**Containers:**
- `ml-models` — Trained Random Forest classifier (`fraud_detection_model.pkl`)
- `extracts` — Generated payroll extract CSVs
- `award-nomination-metrics` — Analytics export files

Container Apps access storage via User-Assigned Managed Identity with the Storage Blob Data Reader/Contributor roles, rather than storage keys (the key is in Key Vault as a fallback).

---

### 9. Container Image Registry

**Service:** Azure Container Registry
**Access:** Private endpoint only (`privatelink.azurecr.io`)
**Images:**
- `award-nomination-api:latest`
- `award-nomination-api:<git-sha>` — immutable, commit-pinned releases

GitHub Actions pushes images using a Service Principal. Container Apps pull images using admin credentials stored in Key Vault.

---

### 10. Azure OpenAI

**Service:** Azure OpenAI
**Model:** GPT-4o deployment
**Access:** Private endpoint only (`privatelink.openai.azure.com`)

Used exclusively by the AI Analytics Agent to answer natural language questions about nomination data. The OpenAI API key and endpoint are stored in Key Vault and injected at runtime.

---

### 11. Identity & Authentication

**Service:** Microsoft Entra ID (Azure AD)
**Managed by:** Terraform (`azuread` provider) for dev and sandbox environments

**Authentication flow:**
1. User accesses SWA at their tenant subdomain
2. MSAL redirects to Microsoft login for that tenant's Azure AD
3. OAuth2 authorization code flow
4. JWT returned and cached in `sessionStorage` (per-origin, tab-isolated)
5. Every API request sends `Authorization: Bearer <jwt>`
6. FastAPI validates JWT signature and audience against Entra ID
7. `tid` claim resolves to internal `TenantId`

**App Roles:**
- `AWard_Nomination_Admin` — full access: fraud stats, audit logs, impersonation, analytics, AI agent
- Default — create nominations, view own history, approve as manager

**Per-tenant:** Each tenant organisation has its own Azure AD tenant. The internal `Tenants.AzureAdTenantId` column maps the `tid` JWT claim to the correct internal tenant record.

---

### 12. Email Notifications

**Service:** Gmail SMTP (primary) / SendGrid (alternative)
**Trigger:** New nomination requiring manager approval

**Email content:**
- Nominator, beneficiary, amount, currency, description
- Approve and Reject buttons with cryptographically signed, single-use action tokens
- Tokens expire after first use — replay attacks are prevented

---

### 13. CI/CD Pipeline

**Service:** GitHub Actions

**Backend workflow** (triggers on `backend/**` changes to `main`):
1. Log in to Azure via Service Principal
2. Build Docker image
3. Tag with `latest` and `git-SHA`
4. Push to Azure Container Registry (via private endpoint)
5. `az containerapp update` — East US
6. `az containerapp update` — West US
7. Health check verification

**Frontend workflow** (triggers on `frontend/**` changes to `main`):
1. `npm run build`
2. Deploy to Azure Static Web Apps via SWA CLI

**Terraform workflows** (manual / environment-gated):
- `terraform plan` on pull requests
- `terraform apply` on merge to `main` (environment approval required for prod)

---

### 14. Infrastructure as Code (Terraform)

**Provider versions:** `azurerm ~> 3.116`, `azuread ~> 2.47`, `random ~> 3.0`
**State backend:** Azure Blob Storage — `awardnomplatform` / `tfstate` — per-environment keys (`dev.tfstate`, `sandbox.tfstate`, `prod.tfstate`)

**Module structure:**

| Module | Resources |
|--------|-----------|
| `networking` | VNets (East + West), ACA subnets, private-endpoint subnets, VNet peering, Private DNS Zones + VNet links |
| `sql` | SQL Server, SQL Database, private endpoint |
| `storage` | Storage account, containers, private endpoint |
| `container-registry` | ACR (Basic SKU), private endpoint |
| `key-vault` | Key Vault, secrets from module outputs, private endpoint |
| `openai` | OpenAI account, GPT-4o deployment, private endpoint |
| `log-analytics` | Log Analytics workspaces (East + West) |
| `container-apps` | CAEs (VNet-injected), Container Apps, KV secret refs, User-Assigned MI |
| `front-door` | Front Door Standard profile, origin group, endpoint, route |
| `static-web-app` | SWA, custom domain, DNS CNAME |
| `grafana` | Azure Managed Grafana, Monitoring Reader role assignments |
| `app-registrations` | Entra ID app registrations (API + frontend), app roles, scopes |

**Multi-phase deployment scripts** (`environments/{env}/`):
- `pre-terraform.ps1` — bootstrap, login verification
- `mid-terraform.ps1` — steps between two `terraform apply` passes (e.g. push initial image to ACR)
- `post-terraform.ps1` — SQL schema deployment, Container App image update, smoke tests

---

### 15. Observability

**Log Analytics Workspaces:** One per region (East US, West US), `PerGB2018` pricing, 30-day retention. Container App Environments stream logs directly.

**Pre-built Kusto saved searches:**
- `GetAwardAPILogs` — API request logs, error rates, latency percentiles
- `GetAwardNominationLogs` — nomination creation and approval events

**Azure Managed Grafana** (Standard SKU):
- System-assigned identity with `Monitoring Reader` on both Log Analytics workspaces
- Pre-wired data sources — no manual configuration required
- Dashboards: API health, fraud trend, region comparison, error rates

---

### 16. AI Analytics Agent & MCP Servers

**AI Analytics Agent** (in-app, admin only):
- OpenAI GPT-4o with a registered tool registry
- Tools: SQL query executor, spending analyser, fraud model inspector
- Current-user context: pronouns (`I`, `me`, `my`) are resolved to the authenticated admin's identity
- Multi-format export: CSV, Excel, PDF, Azure Blob

**MCP Servers** (developer tooling):
- `Nominations SQL Agent MCP` — exposes nomination data via natural language to external AI tools
- `Analytics Export Service MCP` — exposes export endpoints

---

## Data Flow Diagrams

### User Creates a Nomination

```
1.  Employee submits nomination form in React SPA (HTTPS)
2.  SWA → POST /api/nominations/create via Azure Front Door (public HTTPS)
3.  AFD routes to award-api-eastus (private VNet path)
4.  FastAPI validates Bearer JWT with Microsoft Entra ID
5.  TenantId resolved from JWT tid claim
6.  fraud_ml.get_fraud_assessment() called:
    ├─ Download ML model from Blob Storage (private endpoint, cached)
    ├─ Query historical data from SQL (private endpoint)
    ├─ Calculate 20+ fraud features
    ├─ Run SQL rule-based checks (stored procedure)
    ├─ Run Random Forest prediction
    └─ Return fraud score (0–100) + risk level
7.  CRITICAL risk (70–100) → nomination blocked, 400 returned
8.  Acceptable risk → INSERT into SQL Nominations table
9.  INSERT fraud score into FraudScores table
10. Fetch manager email from SQL Users table
11. Send approval email via Gmail SMTP / SendGrid
12. Return 201 Created to frontend
```

### Manager Approves via Email Link

```
1.  Manager clicks Approve link in email
2.  Browser GET /api/nominations/approve?token=<signed-token>
3.  FastAPI validates cryptographic token signature + expiry
4.  Token marked used (single-use enforcement)
5.  UPDATE Nominations SET Status='Approved', ApprovedDate=NOW()
6.  Return confirmation page
```

### Admin Queries the AI Analytics Agent

```
1.  Admin types: "Which department had the highest fraud score last month?"
2.  POST /api/analytics/agent  { "question": "..." }
3.  FastAPI resolves admin identity from JWT
4.  Agent dispatches to SQL query tool with date-range context
5.  OpenAI GPT-4o (private endpoint) generates SQL, executes against SQL DB
6.  Agent returns answer with supporting data
7.  Admin can export results to CSV / Excel / PDF / Blob
```

### Tenant Onboarding

```
1.  New tenant record inserted: dbo.Tenants (TenantId, Domain, AzureAdTenantId, Config)
2.  Terraform: azurerm_dns_cname_record for <subdomain>.terian-services.com
3.  Terraform: azurerm_static_web_app_custom_domain for SWA
4.  Entra ID: App registration configured with new tenant's redirect URIs
5.  Users access https://<tenant>.terian-services.com
6.  Frontend fetches /api/tenant/config → applies theme, locale, currency
```

---

## Security Architecture

### Network Security — Defence in Depth

```
Layer 0 — DNS / Edge
  Azure DNS Zone → AFD endpoint  (HTTPS only, HSTS)

Layer 1 — Public Perimeter
  Azure Front Door Standard
  ├─ WAF (OWASP rule set)
  ├─ DDoS protection (Microsoft edge)
  └─ SSL/TLS termination (TLS 1.2+)

Layer 2 — Application
  Container Apps (VNet-injected, subnet-aca-*)
  ├─ JWT validation on every request
  ├─ RBAC (app roles from JWT claims)
  ├─ TenantId isolation on every DB query
  └─ User-Assigned Managed Identity (no credential in env)

Layer 3 — Data Plane  (private endpoints — no public access)
  SQL · Key Vault · Blob Storage · ACR · OpenAI
  ├─ Traffic stays on Azure backbone
  ├─ Private DNS resolves to 10.x.x.x addresses
  └─ public_network_access = Disabled (target state)

Layer 4 — Secrets
  Azure Key Vault
  ├─ RBAC-only (no legacy access policies)
  ├─ Soft-delete 90 days
  └─ Secret values never in Terraform state (module output injection)
```

### Authentication & Authorization

- **AuthN:** OAuth2 / OpenID Connect via Microsoft Entra ID
- **AuthZ:** Role-based (`AWard_Nomination_Admin` app role in JWT)
- **Token validation:** Every FastAPI request validates JWT signature and audience
- **Admin impersonation:** Logged to `Impersonation_AuditLog` with timestamp, admin UPN, target UPN, action, and client IP

### Data Protection

| Concern | Approach |
|---------|---------|
| In transit | TLS 1.2+ everywhere |
| At rest | Azure SQL TDE, Blob Storage service encryption |
| Secrets | Azure Key Vault, injected via Managed Identity at runtime |
| PII | SQL only; access gated by JWT + TenantId filter |
| ML model | Blob Storage, Managed Identity pull |

---

## High Availability & Disaster Recovery

### High Availability

| Tier | SLA | Mechanism |
|------|-----|-----------|
| Frontend (SWA) | 99.95% | Azure CDN edge, global |
| API Gateway (AFD) | 99.99% | Microsoft global edge |
| Backend (Container Apps) | 99.95% (2 regions) | East + West, AFD failover |
| Database (SQL) | 99.99% | Azure SQL built-in HA |

**Auto-scaling:** Each Container App scales 0–10 replicas based on HTTP concurrency. Zero idle cost when traffic is absent.

**Failover:** AFD health probes (HEAD `/` every 100 s) trigger automatic failover after 3 consecutive failures. RPO ≈ 0 (active-active), RTO < 60 s.

### Disaster Recovery

- **RTO:** < 5 minutes (automatic AFD failover)
- **RPO:** Near-zero (active-active dual-region)
- **SQL backups:** Automated (7–35 days retention, point-in-time restore)
- **ML models:** Versioned in Blob Storage
- **Infrastructure:** Fully reproducible from Terraform + `terraform.tfvars`

---

## Fraud Detection System

### Detection Layers

**Layer 1 — Rule-Based SQL Checks**
- High nomination frequency (> 50 in period)
- Repeated beneficiary pattern (> 5 same person)
- Circular nominations (reciprocal schemes)
- Unusually high amounts (> 2 std dev from user's baseline)
- Rapid approvals (< 1 hour)
- Self-dealing networks (limited graph diversity)

**Layer 2 — ML Classifier (Random Forest, scikit-learn)**
- 20+ engineered features
- User behaviour patterns
- Temporal signals
- Relationship graph features
- Amount anomalies relative to peer groups

**Risk Levels:**

| Level | Score | Action |
|-------|-------|--------|
| CRITICAL | 70–100 | Block nomination automatically |
| HIGH | 50–69 | Flag for manual review |
| MEDIUM | 30–49 | Monitor |
| LOW | 1–29 | Log and proceed |
| NONE | 0 | Normal processing |

**Model lifecycle:** Admins can trigger on-demand retraining via the API. Model metadata (training date, sample count, AUC, feature importance) is available in the admin dashboard.

---

## Scalability

### Current Capacity
- **Container Apps:** 0–10 replicas per region, HTTP concurrency scaling
- **Database:** Serverless Gen5 — auto-pauses when idle, scales on demand
- **Blob Storage:** Effectively unlimited
- **OpenAI:** Configurable TPM (tokens per minute) per deployment

### Growth Path
1. Increase Container App max replicas (Terraform variable)
2. Add regions — Central US, North Europe (new Terraform environment)
3. Upgrade Front Door → Premium (WAF + private link ingress)
4. Add Azure Redis Cache for nomination list and user lookups
5. SQL geo-replication for cross-region read scale
6. Azure Monitor alert rules (Terraform — planned backlog item)

---

## Cost Optimisation

### Approximate Monthly Estimate (dev environment)

| Service | Est. Cost |
|---------|-----------|
| Azure Static Web Apps | Free tier |
| Front Door Standard | ~$35–60 |
| Container Apps (2 regions, low traffic) | ~$20–60 |
| Azure SQL (Serverless, auto-pause) | ~$15–50 |
| Blob Storage | ~$5 |
| Container Registry (Basic) | ~$5 |
| Key Vault | ~$5 |
| Azure OpenAI | Pay-per-token |
| Log Analytics | ~$10–30 |
| Azure Managed Grafana | ~$65 (Standard SKU) |
| **Total dev estimate** | **~$160–310 / month** |

Production costs will be higher (no auto-pause, Premium Front Door, geo-replication).

### Optimisation Levers
- Serverless SQL auto-pauses when idle (60 s delay configured)
- Container Apps scale to zero when no traffic
- SWA Free tier for non-production environments
- Log Analytics retention set to 30 days (min before archiving)

---

## Deployment — Terraform

### Prerequisites
- Azure CLI (`az login`)
- Terraform ≥ 1.5.0
- `terraform.tfvars` populated from `.tfvars.example`

### First-time environment setup

```powershell
# 1. Bootstrap tfstate container (run once)
bash terraform/Scripts/bootstrap.sh

# 2. Pre-terraform steps (create ACR, push placeholder image, etc.)
.\terraform\environments\dev\pre-terraform.ps1

# 3. First apply (networking, SQL, storage, KV, OpenAI, Log Analytics)
cd terraform/environments/dev
terraform init
terraform apply -target=module.networking -target=module.sql -target=module.storage `
  -target=module.key_vault -target=module.openai -target=module.log_analytics

# 4. Mid-terraform steps (push real image to ACR now that private endpoint exists)
.\mid-terraform.ps1

# 5. Full apply (Container Apps, Front Door, SWA, Grafana, App Registrations)
terraform apply

# 6. Post-terraform steps (SQL schema, smoke tests)
.\post-terraform.ps1
```

### Subsequent applies
```powershell
terraform plan   # review changes
terraform apply  # apply
```

---

## Maintenance Tasks

### Daily
- Review Grafana dashboard for error rate and latency anomalies
- Check fraud detection flags in admin dashboard
- Verify both Container App regions are healthy (Front Door health probes)

### Weekly
- Review fraud detection dashboard and high-risk nominations
- Analyse Log Analytics for unusual patterns
- Check GitHub Actions for any failed deployments

### Monthly
- Retrain fraud detection ML model via admin API
- Review and optimise SQL query performance
- Rotate secrets in Key Vault (Key Vault versions automatically)
- Audit impersonation log (`Impersonation_AuditLog`)
- Update Terraform provider versions if minor releases available

### Quarterly
- Review architecture for new Azure services or optimisations
- Full security audit — private endpoint coverage, RBAC assignments
- Disaster recovery drill — test failover to West US
- Cost analysis and rightsizing
- Update fraud ML feature engineering based on new patterns observed

---

## Future Enhancements

| Enhancement | Priority | Notes |
|-------------|----------|-------|
| Front Door → Premium (WAF + Private Link) | High | Removes last public path to Container Apps |
| Azure Monitor alert rules (Terraform) | High | Fraud spike, 5xx rate, latency thresholds |
| SQL Managed Identity auth | High | Replace SQL password with MI-based auth |
| SQL geo-replication | Medium | Cross-region read scale + DR |
| Azure Redis Cache | Medium | Reduce SQL load for user/nomination lookups |
| Azure API Management | Medium | Rate limiting, developer portal, advanced routing |
| Real-time notifications | Low | Azure SignalR for live approval updates |
| Power BI embedded dashboards | Low | Executive reporting |
| Azure Functions — async processing | Low | Batch payroll processing, scheduled reports |
| Enhanced ML models | Low | AutoML, deep learning for fraud detection |

---

## Support & Documentation

- **Architecture diagram:** `Documentation/architecture_diagram.mmd` (Mermaid)
- **Sequence diagrams:** `Documentation/sequence_diagram.mmd`
- **API documentation:** Swagger UI at `/docs` (each Container App)
- **Terraform:** `terraform/` — modules and environment configs
- **CI/CD:** `.github/workflows/`
- **Source code:** GitHub — `David64_Award_Nominations`
- **DevOps backlog:** `https://dev.azure.com/Terian-Services/Award_Nomination`
- **Grafana:** Azure Managed Grafana instance (per-environment)

---

**Document Version:** 2.0
**Last Updated:** March 2026
**Next Review:** June 2026
