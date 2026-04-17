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
