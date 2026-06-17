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
Used to assign the HRBP role to users who review flagged nominations.

| Column      | Type          | Notes                                                      |
|-------------|---------------|------------------------------------------------------------|
| UserRoleId  | INT IDENTITY  | Primary Key                                                |
| UserId      | INT           | FK → Users.UserId                                          |
| Role        | NVARCHAR(50)  | Exact values: HRBP                                         |
| AssignedAt  | DATETIME      | When the role was assigned                                 |
| AssignedBy  | INT           | FK → Users.UserId (who assigned the role); may be NULL     |

Tenant isolation: join through Users to scope by TenantId.

Example — find all HRBP users for the current tenant:
```sql
SELECT u.FirstName, u.LastName, u.userEmail
FROM   dbo.UserRoles ur
JOIN   dbo.Users u ON u.UserId = ur.UserId
WHERE  ur.Role     = 'HRBP'
  AND  u.TenantId  = <TenantId>
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
