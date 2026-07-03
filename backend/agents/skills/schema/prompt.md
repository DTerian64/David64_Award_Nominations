# Schema Skill — Database Table Definitions

## dbo.Tenants
| Column               | Type           | Notes                                                        |
|----------------------|----------------|--------------------------------------------------------------|
| TenantId             | INT IDENTITY   | Primary Key                                                  |
| TenantName           | NVARCHAR(256)  | Human-readable organisation name                             |
| AzureAdTenantId      | NVARCHAR(36)   | Azure AD / Entra ID tenant GUID                              |
| Config               | NVARCHAR(MAX)  | JSON blob of tenant configuration; may be NULL               |
| Domain               | NVARCHAR(253)  | Canonical public hostname, e.g. acme-awards.terianix.ai |
| fallback_admin_email | NVARCHAR(256)  | Emailed when no HRBP is configured for the tenant            |
| Site_URL             | NVARCHAR(256)  | Frontend portal URL used in outbound email hyperlinks        |

## dbo.UserRoles
Application-level role assignments managed within the app (not Azure AD).
Used to assign elevated roles to users beyond the default employee view.

| Column      | Type          | Notes                                                          |
|-------------|---------------|----------------------------------------------------------------|
| UserRoleId  | INT IDENTITY  | Primary Key                                                    |
| UserId      | INT           | FK → Users.UserId                                              |
| TenantId    | INT           | FK → Tenants.TenantId — denormalised for query convenience     |
| Role        | NVARCHAR(50)  | Exact values: HRBP, Support, PayrollBP                         |
| AssignedAt  | DATETIME      | When the role was assigned                                     |
| AssignedBy  | INT           | FK → Users.UserId (who assigned the role); may be NULL         |

Role meanings:
- **HRBP** — reviews nominations held in PendingHRBPReview by the fraud model
- **Support** — receives payroll failure alert emails when the broker cannot submit to the provider
- **PayrollBP** — may look up employee payroll data via the Payroll tab

Tenant isolation: filter directly on TenantId (no Users join needed).

Example — find all HRBP users for the current tenant:
```sql
SELECT u.FirstName, u.LastName, u.userEmail
FROM   dbo.UserRoles ur
JOIN   dbo.Users u ON u.UserId = ur.UserId
WHERE  ur.Role     = 'HRBP'
  AND  ur.TenantId = <TenantId>
```

## dbo.Users
| Column             | Type           | Notes                                          |
|--------------------|----------------|------------------------------------------------|
| UserId             | INT            | Primary Key                                    |
| userPrincipalName  | NVARCHAR(100)  |                                                |
| FirstName          | NVARCHAR(50)   |                                                |
| LastName           | NVARCHAR(50)   |                                                |
| Title              | NVARCHAR(100)  | Also referred to as Department in user queries |
| ManagerId          | INT            | FK → Users.UserId (self-referencing)           |
| userEmail          | NVARCHAR(100)  |                                                |
| TenantId           | INT            | FK → Tenants.TenantId — MUST be filtered on every query |

## dbo.Nominations
| Column                | Type           | Notes                                            |
|-----------------------|----------------|--------------------------------------------------|
| NominationId          | INT IDENTITY   | Primary Key                                      |
| NominatorId           | INT            | FK → Users.UserId                                |
| BeneficiaryId         | INT            | FK → Users.UserId                                |
| ApproverId            | INT            | FK → Users.UserId                                |
| Status                | NVARCHAR(20)   | Exact values: Pending, Approved, Rejected, Paid  |
| Amount                | INT            |                                                  |
| Currency              | NVARCHAR(10)   | e.g. USD, KRW                                    |
| NominationDescription | NVARCHAR(500)  |                                                  |
| NominationDate        | DATE           |                                                  |
| ApprovedDate          | DATETIME2      |                                                  |
| PayedDate             | DATETIME2      |                                                  |

## dbo.P2P_FraudScores
Peer-to-peer fraud score written at nomination submission time.
Uses features knowable at submission time (nominator/beneficiary behaviour, amount, category).

| Column       | Type           | Notes                                              |
|--------------|----------------|----------------------------------------------------|
| P2PScoreId   | INT IDENTITY   | Primary Key                                        |
| NominationId | INT            | FK → Nominations.NominationId (UNIQUE)             |
| FraudScore   | INT            | 0–100; higher = more suspicious                    |
| RiskLevel    | NVARCHAR(20)   | Exact values: NONE, LOW, MEDIUM, HIGH, CRITICAL, UNKNOWN |
| FraudFlags   | NVARCHAR(500)  | Comma-separated human-readable fraud signals       |
| CreatedAt    | DATETIME       | When the score was written                         |

Note: P2P_FraudScores has no TenantId column — tenant isolation is enforced by
joining through Nominations → Users:
`JOIN dbo.Users u ON u.UserId = n.NominatorId WHERE u.TenantId = <TenantId>`

## dbo.Appr_FraudScores
Approver-behaviour fraud score written by the weekly batch job after nominations are Paid.
Uses post-decision features (approval speed, payment speed).

| Column       | Type           | Notes                                              |
|--------------|----------------|----------------------------------------------------|
| ApprScoreId  | INT IDENTITY   | Primary Key                                        |
| NominationId | INT            | FK → Nominations.NominationId (UNIQUE)             |
| FraudScore   | INT            | 0–100; higher = more suspicious                    |
| RiskLevel    | NVARCHAR(20)   | Exact values: NONE, LOW, MEDIUM, HIGH, CRITICAL    |
| FraudFlags   | NVARCHAR(500)  | Comma-separated warning flags                      |
| CreatedAt    | DATETIME       | When the score was written                         |

Note: same tenant isolation pattern as P2P_FraudScores — join through Nominations → Users.

## dbo.DemoRegistrationRequests
Public self-registration audit log written by `POST /api/demo/request`
(the form on https://demo-awards.terianix.ai/demo/request).
One row per accepted demo-access request — used for audit, rate limiting
(by email and by IP), and de-duplication. Contains PII.

| Column        | Type           | Notes                                              |
|---------------|----------------|----------------------------------------------------|
| Id            | INT IDENTITY   | Primary Key                                        |
| FirstName     | NVARCHAR(128)  |                                                    |
| LastName      | NVARCHAR(128)  |                                                    |
| Email         | VARCHAR(256)   | Original invitation email (not the #EXT# UPN)      |
| IsAdmin       | BIT            | True if admin role was requested at signup         |
| AadObjectId   | VARCHAR(36)    | Guest OID in the Demo tenant; NULL if Graph failed |
| RequestIp     | VARCHAR(64)    | Originating IP for rate-limit accounting           |
| RequestedAt   | DATETIME       | Server-side timestamp                              |

**Tenant isolation: not directly queryable.** This table is global to the
application — every demo signup, across every tenant context, is logged
here. It has no `TenantId` column and no FK path to `Users` / `Tenants`, so
the tenant-isolation guard in `query_database` will reject any SQL written
against it (the SQL contains no `TenantId` reference). This is intentional:
the table contains other visitors' names, emails, and IP addresses, which
must not be exposed to in-tenant users.

If an admin-facing use case needs this data, add a dedicated tool that
checks the caller's admin role before bypassing the tenant guard — do not
relax the guard on `query_database`.

## dbo.payroll_providers
One row per configured payroll provider instance. The `name` column is the
type discriminator used by the payroll broker to route to the correct API
client. Multiple rows may share the same `name` (e.g. two tenants both on
Gusto), each with a different `company_id_at_provider`.

Linked to tenants via `dbo.Tenants.payroll_provider_id` (NULL = no payroll
configured for that tenant).

| Column                 | Type           | Notes                                                                                         |
|------------------------|----------------|-----------------------------------------------------------------------------------------------|
| id                     | INT IDENTITY   | Primary Key                                                                                   |
| name                   | VARCHAR(50)    | Provider type discriminator: gusto, workday, adp, …                                           |
| display_name           | NVARCHAR(100)  | Human-readable label, e.g. "Gusto – Sandbox Inc."                                            |
| company_id_at_provider | VARCHAR(100)   | Provider's own company reference (Gusto UUID, Workday tenant name, ADP code); NULL until OAuth completes |
| provider_config        | NVARCHAR(MAX)  | JSON blob for provider-specific extras (e.g. Workday per-tenant host URL); may be NULL        |
| api_base_url           | VARCHAR(255)   | Override for the provider's API root; NULL = use hardcoded default                            |
| oauth_base_url         | VARCHAR(255)   | Override for the provider's OAuth root; may be NULL                                           |

Tenant isolation: join through Tenants.
```sql
SELECT pp.*
FROM   dbo.payroll_providers pp
JOIN   dbo.Tenants t ON t.payroll_provider_id = pp.id
WHERE  t.TenantId = <TenantId>
```

## dbo.payroll_submissions
One row per payroll submission attempt for a nomination. Created by the
payroll broker when it submits an off-cycle bonus to the provider; updated
when the provider webhook confirms acceptance or rejection.

| Column               | Type            | Notes                                                                                           |
|----------------------|-----------------|-------------------------------------------------------------------------------------------------|
| id                   | INT IDENTITY    | Primary Key                                                                                     |
| nomination_id        | INT             | FK → Nominations.NominationId                                                                   |
| provider_id          | INT             | FK → payroll_providers.id                                                                       |
| provider_payroll_ref | VARCHAR(100)    | Provider's reference for this payroll run (e.g. Gusto UUID); NULL if submission failed before a ref was assigned |
| status               | VARCHAR(50)     | Exact values: submitted, accepted, rejected                                                     |
| submitted_at         | DATETIME        | When the broker first attempted submission (UTC)                                                |
| completed_at         | DATETIME        | When the provider confirmed acceptance; NULL if still pending or rejected                       |
| reason               | NVARCHAR(1000)  | Provider-supplied rejection message; NULL on success                                            |

Status lifecycle: submitted → accepted (completed_at set) or rejected (reason set).

Tenant isolation: join through Nominations → Users.
```sql
SELECT ps.*
FROM   dbo.payroll_submissions ps
JOIN   dbo.Nominations n ON n.NominationId = ps.nomination_id
JOIN   dbo.Users u       ON u.UserId = n.NominatorId
WHERE  u.TenantId = <TenantId>
```
