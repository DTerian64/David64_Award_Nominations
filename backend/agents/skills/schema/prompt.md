# Schema Skill — Database Table Definitions

## dbo.Tenants
| Column           | Type           | Notes                                              |
|------------------|----------------|----------------------------------------------------|
| TenantId         | INT IDENTITY   | Primary Key                                        |
| TenantName       | NVARCHAR(256)  | Human-readable organisation name                   |
| AzureAdTenantId  | NVARCHAR(36)   | Azure AD / Entra ID tenant GUID                    |
| Config           | NVARCHAR(MAX)  | JSON blob of tenant configuration; may be NULL     |

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

## dbo.FraudScores
| Column       | Type           | Notes                                              |
|--------------|----------------|----------------------------------------------------|
| ScoreId      | INT IDENTITY   | Primary Key                                        |
| NominationId | INT            | FK → Nominations.NominationId                      |
| FraudScore   | INT            | 0–100; higher = more suspicious                    |
| RiskLevel    | NVARCHAR(20)   | Exact values: NONE, LOW, MEDIUM, HIGH, CRITICAL    |
| FraudFlags   | NVARCHAR(500)  | Comma-separated human-readable fraud signals       |
| ScoredAt     | DATETIME2      | When the score was written                         |

Note: FraudScores has no TenantId column — tenant isolation is enforced by
joining through Nominations → Users:
`JOIN dbo.Users u ON u.UserId = n.NominatorId WHERE u.TenantId = <TenantId>`

## dbo.DemoRegistrationRequests
Public self-registration audit log written by `POST /api/demo/request`
(the form on https://demo-awards.terian-services.com/demo/request).
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
