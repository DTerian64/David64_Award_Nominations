# ADR-0001: SQL Database Access via Managed Identity

**Status:** Accepted
**Date:** 2026-07-10 (Proposed) · 2026-07-10 (Accepted)
**Deciders:** David Terian (Engineering), Security/Compliance (SOC 2 owner)

## Context

Every Azure Container App (ACA) in the Award Nomination platform currently connects to
the Azure SQL Database using a single set of SQL authentication credentials tied to a
**personal account** (`dterian64` + static password). The connection string, including
the password, is distributed to all containers.

This is a material finding for our upcoming **SOC 2** certification and is poor security
practice regardless of the audit:

- **Tied to an individual.** Access cannot be de-provisioned when the person leaves
  without breaking production. The database has no way to distinguish "the app" from
  "David."
- **Static, shared secret.** The password does not rotate, is stored in application
  configuration, and is shared across every workload. Any leak compromises everything.
- **Over-privileged.** A personal login typically carries broad rights well beyond what
  a runtime workload needs.
- **No per-workload attribution.** Audit logs show every ACA as the same principal, so
  we cannot answer "which service did this?"

The platform already provisions a **user-assigned Managed Identity per container**
(`id-award-api-primary-sandbox`, `id-award-api-secondary-sandbox`,
`id-award-auxiliary-sandbox`, `id-award-fraud-analytics-sandbox`,
`id-award-integrity-check-sandbox`, `id-award-payroll-broker-sandbox`, …). These
identities are currently unused for database access — the opportunity is to make them
the *only* way the platform reaches SQL.

A complicating factor: **all schema changes (DDL) are performed by Alembic**, currently
run from within the backend at container startup. If the runtime identity is granted the
DDL rights Alembic needs, every traffic-serving container can also alter or drop tables —
which would undermine the least-privilege model. Separation of duties must be designed in.

### Constraints and non-functional requirements

- Must remove all static SQL credentials from application configuration.
- Runtime workloads must hold **least privilege** (data plane only, no schema rights).
- Schema migrations must remain automated (Alembic) but auditable and gated.
- SQL authentication should be **fully disabled** on the server once cut over
  (no personal-credential break-glass path — an explicit decision, see Consequences).
- Pattern is designed in **sandbox first**, then promoted unchanged to production.

## Decision

Move all ACA-to-SQL access to **Entra ID (Azure AD) authentication using the existing
per-container user-assigned Managed Identities**, governed through Entra security groups
mapped to least-privilege database roles. Separate the migration path from the runtime
path so that DDL rights never live on a traffic-serving identity.

Specifically:

1. **Runtime access** — an Entra security group `sql-app-readwrite` containing the
   per-container Managed Identities, mapped to a contained database user granted
   `db_datareader` + `db_datawriter` only.
2. **Migration access** — a separate Entra security group `sql-migrations` containing a
   dedicated CI/CD service principal, mapped to a contained database user granted
   `db_ddladmin` (+ `db_datareader`/`db_datawriter` for data migrations). No runtime
   identity is a member.
3. **Administration** — the Entra admin on the SQL server is a group (`sql-admins`),
   **not** a personal account.
4. **Migrations run in the CI/CD pipeline** from a dedicated standalone project —
   **`schema-migration/`** (Alembic config + `versions/`, models-free), owned by no runtime
   service. A one-shot job runs `alembic upgrade head` authenticated as the `sql-migrations`
   principal via OIDC federated credentials, triggered by `schema-migration/**` changes and
   independent of runtime rollout. No runtime container is redeployed for a schema-only change.
5. **SQL authentication is disabled** on the server after cutover; connection strings
   carry no secret.

All Azure/Entra entities are provisioned via Terraform (`terraform/modules/sql-access`,
wired per environment, e.g. `terraform/environments/sandbox/sql-access.tf`) — not standalone
scripts. Groups are **environment-scoped** (`sql-app-readwrite-<env>`, `sql-migrations-<env>`,
`sql-admins-<env>`) so sandbox identities can never obtain production-database access. The
only non-Terraform step is the in-database `CREATE USER`/role grants (data-plane objects),
applied by the `schema-migrations` pipeline. Group names below omit the `-<env>` suffix for
readability.

## Options Considered

### Option A: Per-container Managed Identity via Entra security groups (chosen)

Runtime MIs join `sql-app-readwrite`; a dedicated CI/CD principal joins `sql-migrations`.
Access is managed by group membership; roles enforce least privilege; DDL is isolated to
the pipeline.

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — group + role setup, pipeline migration step |
| Cost | None (no secret store required for DB auth) |
| Scalability | High — new ACAs just join the group |
| Team familiarity | Medium — Alembic moves out of app startup |
| SOC 2 fit | Strong — no secrets, least privilege, separation of duties, group-based access reviews |

**Pros:**
- No static credentials anywhere; nothing to rotate or leak.
- Least privilege for runtime; DDL isolated to a gated pipeline identity.
- Access reviews are "who is in `sql-app-readwrite` / `sql-migrations`" — clean audit evidence.
- Per-identity attribution in database audit logs.
- New containers onboard by group membership, no per-resource grants.

**Cons:**
- Alembic must move out of container startup into the pipeline (one-time refactor).
- Group-based token claims can lag slightly on membership change (propagation delay).
- Requires an Entra admin group and initial `CREATE USER ... FROM EXTERNAL PROVIDER` setup.

### Option B: Managed Identity granted directly (no groups)

Each MI mapped individually as a database user with direct role grants.

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — but grows linearly with each new ACA |
| Cost | None |
| Scalability | Low — every new container needs a manual `CREATE USER` + grant |
| Team familiarity | Medium |
| SOC 2 fit | Adequate but weaker — access review must enumerate every user grant |

**Pros:** Removes static secrets; least privilege still achievable; no group-propagation delay.
**Cons:** Access management does not centralize; onboarding/offboarding is per-identity;
audit reviews are more tedious.

### Option C: Keep SQL auth but move the password to Key Vault (rejected)

Replace the personal login with a dedicated SQL login whose password lives in Key Vault,
fetched by each ACA's MI.

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low |
| Cost | Key Vault (minimal) |
| Scalability | Medium |
| Team familiarity | High |
| SOC 2 fit | Weak — a shared static secret still exists |

**Pros:** Smallest change; removes the *personal* account; secret is no longer in app config.
**Cons:** A rotatable shared secret still exists and must be rotated and audited; still no
per-workload attribution; does not achieve the "no standing DB secret" bar. Rejected as a
half-measure that leaves the core finding open.

## Trade-off Analysis

The central trade-off is **operational refactor cost (Option A) vs. residual audit risk
(Options B and C).**

Option C is the cheapest change but leaves a shared static secret in place, which is
essentially the finding we are trying to close — rejected.

Between A and B, both remove secrets and achieve least privilege. B avoids
group-propagation nuances but pushes all access management down to per-identity grants,
which does not scale and makes SOC 2 access reviews manual and error-prone. A centralizes
access into two auditable groups at the cost of a small refactor (moving Alembic) and
minor token-propagation latency on membership changes. For a platform with a growing set
of containers and an active audit, **A's centralized, reviewable access model is worth the
refactor** — the group boundary *is* the control the auditor wants to see.

The migration-path separation is not really optional: whichever runtime model we pick, DDL
must not live on a traffic-serving identity. Running Alembic as a gated pipeline step under
the `sql-migrations` principal gives us both least privilege and an approval + audit trail
for every schema change — strong evidence for change-management controls.

### Schema ownership: a standalone migration project (not the backend)

Schema is owned by a **dedicated standalone project — `schema-migration/`** — not by any
runtime service. This resolves a coupling problem: if migrations lived in the backend, a
schema change driven by another service (e.g. `integrity-check` needing a new column) would
be a commit to the backend's repo and would ride the backend's deploy lifecycle, even though
the backend code did not change.

`schema-migration/` contains the Alembic config and the `versions/` history only. It is
**models-free**: migrations are hand-written and applied with `alembic upgrade head`, so
`target_metadata` is `None`, the project has no ORM dependency, and it does not import the
backend. (Autogenerate is not used; the backend's ORM models and `create_all_tables()` were
removed outright — see Consequences.) Any service's schema need (backend, integrity-check,
fraud-analytics, …) flows through this one project and its one gated pipeline.

The pipeline migration step is **not attached to any running ACA**. It is an ephemeral,
one-shot job with a distinct identity and lifecycle:

- **Identity:** OIDC federated credential → `sql-migrations` service principal
  (`db_ddladmin`). None of the runtime MIs participate.
- **Lifecycle:** runs `alembic upgrade head` on `schema-migration/**` changes, then exits.
  Not long-lived.

Runtime containers run the already-migrated schema, never hold DDL rights, and are **not
redeployed for a schema-only change**.

Alternatives rejected: keeping Alembic **in the backend** (couples schema ownership and
deploy cadence to one runtime service); **per-service migrations** (conflicting histories and
ordering problems on a single shared database).

## Consequences

**Easier**
- No secret rotation, storage, or leakage risk for database access.
- SOC 2 access reviews reduce to group-membership review of the SQL-access Entra groups.
- Per-identity attribution in SQL audit logs.
- New ACAs onboard by joining `sql-app-readwrite`.
- Schema changes are approval-gated with a pipeline audit trail.

**Harder / changed**
- Alembic must be removed from container startup and run from the standalone
  `schema-migration/` project as a pipeline step (refactor + deploy-flow change).
- The backend's ORM models and `create_all_tables()` are removed outright; the runtime uses
  raw SQL only. Migrations are hand-written, so no ORM or shared-models package is needed.
- Requires Entra admin group, contained users, and role grants to be provisioned before cutover.
- Local/developer workflows that relied on the shared login must move to individual Entra
  logins (developers authenticate as themselves against sandbox).
- No personal-credential break-glass: emergency DB access must go through a documented,
  audited Entra path (e.g. temporary membership in `sql-admins` with approval), not a
  standing secret.

**To revisit**
- Whether group-claim propagation latency ever affects deploys; if so, consider Option B's
  direct grants for the most latency-sensitive workloads.
- Read-only workloads (e.g. `id-award-fraud-analytics`, `id-award-integrity-check` if they
  only read) may warrant a `sql-app-readonly` group (`db_datareader` only) for tighter least
  privilege.
- Cross-region identities (`id-award-api-secondary-sandbox` in West US 2) — confirm token
  audience/endpoint behavior against the SQL server's region.

## Reference Implementation Notes

Runtime connection string (no secret):

```
Server=tcp:<server>.database.windows.net;Database=<db>;Authentication=Active Directory Managed Identity;User Id=<client-id-of-user-assigned-MI>;
```

Database setup (run once, as the `sql-admins` Entra admin):

```sql
-- Runtime group: data plane only
CREATE USER [sql-app-readwrite] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [sql-app-readwrite];
ALTER ROLE db_datawriter ADD MEMBER [sql-app-readwrite];

-- Migration group: schema changes, no user/permission management
CREATE USER [sql-migrations] FROM EXTERNAL PROVIDER;
ALTER ROLE db_ddladmin  ADD MEMBER [sql-migrations];
ALTER ROLE db_datareader ADD MEMBER [sql-migrations];
ALTER ROLE db_datawriter ADD MEMBER [sql-migrations];
```

Because grants are role-based, tables created by `sql-migrations` are automatically
readable/writable by `sql-app-readwrite` (`db_datareader`/`db_datawriter` cover all tables
regardless of creator) — no per-table `GRANT` maintenance.

## Action Items

1. [ ] `terraform apply` the `sql-access` + `sql` modules (`environments/sandbox`): creates the
       three env-scoped groups, joins the six runtime MIs to `sql-app-readwrite-<env>`, creates
       BOTH secretless CI identities (`schema-migrations-cicd`, `sql-admin-cicd`) with OIDC
       federated credentials, and sets the SQL server Entra admin to `sql-admins-<env>`. Set
       `github_org_repo` in tfvars first.
2. [ ] Configure GitHub Environments: base `<env>` gets `SCHEMA_MIGRATIONS_CLIENT_ID`,
       `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` (vars) + `SQL_SERVER`, `SQL_DATABASE` (secrets).
       Create protected `<env>-dbadmin` (required reviewers) with `SQL_ADMIN_CICD_CLIENT_ID` and
       the same tenant/subscription/SQL values.
3. [ ] Run the **Bootstrap DB Access** workflow once (`bootstrap-db-access.yaml`) — creates the
       contained users `sql-app-readwrite-<env>` (reader/writer) and `sql-migrations-<env>`
       (db_ddladmin). Idempotent; grants are inlined in the workflow (no side script).
4. [x] Alembic moved to the standalone `schema-migration` project (models-free `env.py`);
       ORM models + `create_all_tables()` removed from the backend. Migrations are hand-written,
       so no ORM/shared-models package is needed.
5. [ ] `deploy-schema-migration.yaml` applies `alembic upgrade head` as the `sql-migrations`
       principal, triggered by `schema-migration/**` changes, independent of runtime rollout.
6. [ ] Update runtime connection strings to `Authentication=Active Directory Managed Identity` (remove password).
7. [ ] Validate end-to-end in sandbox (runtime read/write works; runtime cannot run DDL; migrations succeed).
8. [ ] Disable SQL authentication: set `sql_entra_admin_only = true` and `terraform apply`; retire the `dterian64` login.
9. [ ] Document the break-glass procedure via approved `sql-admins-<env>` membership.
10. [ ] Promote the identical pattern to production (add `environments/prod/sql-access.tf` + a `production-dbadmin` environment).
