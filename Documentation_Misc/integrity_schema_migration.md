# Integrity SQL schema migration

Status: Implementation complete locally; coordinated sandbox cutover pending  
Last updated: 2026-09-29

## 1. Decision summary

Move the integrity-model database objects from `dbo` to a dedicated SQL Server schema named `integrity`.

The selected schema name is **`integrity`**, not an abbreviation such as `int`, `igr`, or `itg`:

- It states the ownership boundary clearly in SQL, telemetry, and support conversations.
- It is consistent with the product's move from fraud terminology to integrity terminology.
- `INT` is a SQL data type and appears in Microsoft's future-keyword list. Although `[int]` can be escaped, it would require permanent quoting and creates avoidable ambiguity for people and tools.

This is a coordinated database and application migration, not only a table rename. The current implementation contains hard-coded `dbo.<table>` references in several independently deployed services. The selected strategy is a one-step sandbox cutover: pause every consumer, transfer the database objects, deploy application revisions that directly reference `integrity`, validate them, and then resume processing.

There will be no transitional container setting, runtime schema resolver, or permanent compatibility alias. The old code and the new database layout must never run together, and the new code must not start before the database migration commits.

Do not combine the physical schema transfer with the first production run of the two-replica analytics job.

## 2. Goals

- Give integrity-model data a clear ownership boundary separate from general application tables in `dbo` and operational history in `ops`.
- Preserve data, object identities, graph semantics, foreign keys, indexes, temporal history, and retention settings.
- Allow a controlled cutover and a practical rollback.
- Replace active `dbo` qualifiers for the migrated objects with the permanent `integrity` schema.
- Prevent new unqualified or `dbo`-qualified references to the migrated objects.

## 3. Non-goals

- Renaming tables or columns.
- Moving the analytics execution-history tables out of `ops`.
- Moving core application tables such as `Tenants`, `Users`, or `Nominations` out of `dbo`.
- Rewriting historical Alembic migrations.
- Reintroducing the legacy integrity score tables removed by migration `0053`.
- Changing model behavior, policies, thresholds, or job parallelism as part of this migration.

## 4. Proposed object scope

The following live objects are in scope, subject to the preflight inventory against each deployed database:

| Object | Type or role | Important considerations |
|---|---|---|
| `GNN_UserEmbeddings` | GNN serving data | Used by training and scoring paths |
| `GNNScoringPolicies` | GNN policy | Referenced by job and integrity services |
| `GraphPatternFindings` | Graph findings | Terraform currently supplies this table name to a consumer |
| `GraphScoringChangeRequests` | Graph policy workflow | Related to graph policies and parameters |
| `GraphScoringPatternParameters` | Graph policy parameters | Preserve constraints and foreign keys |
| `GraphScoringPolicies` | Graph policy | Parent object for policy-related data |
| `IntegrityComponentStatus` | System-versioned temporal table | Current and history tables must move together |
| `IntegrityComponentStatus_History` | Temporal history table | Preserve the explicit history-table link and 24-month retention |
| `IntegrityDecisionResults` | Integrity decisions | Used by backend and integrity processing |
| `NomGraph_NominationEmbedding` | Graph/model data | Verify runtime and reset-script references |
| `NomGraph_Nominated` | SQL Graph edge table | Preserve and verify `is_edge` metadata |
| `NomGraph_Person` | SQL Graph node table | Preserve and verify `is_node` metadata |
| `UserGraphFlags` | Graph-derived flags | Used by application/integrity paths |
| `ApproverPairFlags` | Approver-behavior integrity signals | Move now to support the planned approver-behavior integrity check alongside P2P nomination checks |

### Explicitly out of scope

- `ops.IntegrityAnalyticsJobRuns`
- `ops.IntegrityAnalyticsTenantRuns`
- `ops.IntegrityAnalyticsStageAttempts`
- `dbo.Tenants`, `dbo.Users`, `dbo.Nominations`, and other core transactional tables
- `Nomination_Logs` and general audit tables
- Removed legacy tables: `GNN_FraudScores`, `Graph_FraudScores`, `P2P_FraudScores`, and `FraudDecisionResults`

`ApproverPairFlags` does not yet have a known runtime consumer in this repository, but it is intentionally retained. It will support the planned approver-behavior integrity check, alongside the existing P2P nomination integrity checks, and must move to `integrity` with the other approved objects.

## 5. Repository blast radius

The initial repository scan found more than 700 textual references associated with these objects. That includes historical migrations and documentation, so the number is not a count of required edits. The actionable surface is approximately:

- 25 runtime, configuration, script, and prompt files
- 15 test files
- 26 current or historical documentation/prompt artifacts
- 29 historical migrations that mention one or more objects

Historical migrations are an immutable record and must not be edited. The physical move should be implemented as a new migration after `0071`, expected to be `0072` if no other migration is added first.

### Runtime consumers

| Component | Representative location | Required change |
|---|---|---|
| Backend API and agent tools | `backend/utils/sqlhelper2.py` and backend tool/prompt modules | Use the configured integrity schema for all in-scope objects |
| Analytics job | `fraud-analytics-job/systems/award_nominations/modeling/graph.py` and model stages | Use the configured integrity schema; retain `ops` for run history |
| Integrity check worker | `integrity-check/utils/db.py` | Use the configured integrity schema for reads and writes |
| Integrity check extension | `integrity-check-extension/utils/db.py` | Use the configured integrity schema for reads and writes |
| Auxiliary service | `auxiliary-service/utils/db.py` | Update integrity/model reads |
| Terraform | Environment and service modules, including `GRAPH_FINDINGS_TABLE` | Remove transient schema selection; use the permanent integrity-qualified object where configuration is still required |
| Maintenance and demo scripts | Synthetic reset/database helpers, demo seeding, and GNN validation | Update all live references directly to `integrity` |
| Tests | Service and migration tests | Exercise the final `integrity` layout and migration downgrade |

The frontend and `integrity-engine-core` did not show direct database-object references in the initial scan. They should remain in the validation inventory in case runtime configuration or generated SQL introduces an indirect dependency.

### External consumers

Repository search cannot identify external SQL clients. Before cutover, check for:

- Power BI or other reporting datasets
- notebooks and analyst queries
- scheduled SQL jobs
- stored procedures, views, functions, and triggers in the database
- external integrations and support runbooks

Owners of every direct SQL consumer must update it to `integrity.<object>` before the coordinated cutover. An unidentified consumer may fail immediately after the transfer because no compatibility layer will remain in `dbo`.

## 6. Application cutover design

Application code will use the final schema directly:

```python
INTEGRITY_SCHEMA = "[integrity]"
```

Each deployable codebase may centralize this permanent constant in its database access layer, such as `db.py`, to avoid repeated string literals. It is not read from container configuration and has no `dbo` fallback. Table names also remain fixed in application code, and all data values continue to use normal SQL parameters.

Only the approved integrity objects use this schema constant. References to `ops`, core `dbo` tables, and other schemas remain explicit. For example, analytics run history remains under `[ops]` while graph findings become `[integrity].[GraphPatternFindings]`.

The existing `GRAPH_FINDINGS_TABLE` configuration should be removed if it exists only to select this database object. The application now uses the permanent `integrity.GraphPatternFindings` contract directly.

All new application images and migration artifacts must be built and tested before the maintenance window, but the new application revisions must not be activated against the old `dbo` layout.

## 7. Database migration mechanics

Use `ALTER SCHEMA ... TRANSFER` so SQL Server moves the existing objects instead of copying data into replacements. This preserves the underlying object identity, but it has several consequences that the migration must handle explicitly.

### 7.1 Temporal table pair

`IntegrityComponentStatus` is system-versioned and is linked to `IntegrityComponentStatus_History`. The migration must run in a transaction and:

1. Disable system versioning.
2. Transfer the history table.
3. Transfer the current table.
4. Re-enable system versioning with the explicit `integrity.IntegrityComponentStatus_History` table.
5. Restore the 24-month history retention and request a data-consistency check.
6. Verify the current/history link from SQL Server metadata.

Do not omit the explicit history table when re-enabling versioning; SQL Server could otherwise create or select a different history table.

### 7.2 Object permissions

SQL Server drops permissions associated directly with an object when it is transferred to another schema. Before migration, inventory all grants and denies on the target objects.

The current runtime principal's membership in `db_datareader` and `db_datawriter` should continue to cover user tables in the new non-system schema, but this does not preserve object-specific permissions. The migration must either:

- fail preflight when unexpected object-specific permissions exist and require an explicit permission plan, or
- capture and deliberately reapply the approved grants/denies after transfer.

Also verify that the migration identity has the permissions required to alter the target schema and control the transferred objects, including both temporal tables.

The Container Apps migration job runs through the `sql-migrations-<env>` database principal. `db_ddladmin` alone cannot perform all system-versioning operations. The SQL access bootstrap therefore adds this principal to an application-specific `award_schema_migrator` database role with `CONTROL` on the three application-managed schemas: `dbo`, `integrity`, and `ops`.

This is intentionally narrower than `db_owner`: it grants no server-level authority and no authority over schemas outside those three namespaces. It supplies the permissions needed for temporal enable/disable, `ALTER SCHEMA ... TRANSFER`, ordinary application DDL, and downgrade. Migration `0072` explicitly requires schema-level `CONTROL` on both its source and destination schemas so an underprivileged identity fails before system versioning is disabled.

An Entra SQL administrator must run `scripts/soc2-sql-managed-identity/db-access-grants.sql` once for each environment. The script is idempotent, creates `integrity` and `ops` when absent, creates the custom role, grants schema control, and adds the existing migration group. The migration job remains the execution identity for migration `0072`; no manual Alembic execution or temporary elevation is required.

### 7.3 Database modules and dependencies

Moving an object does not rewrite schema-qualified references inside stored procedures, views, functions, triggers, computed expressions, or other modules. Preflight must enumerate database dependencies and either update them in the same maintenance window or stop the migration.

Cross-schema foreign keys normally follow object identity through a transfer. Nevertheless, post-migration validation must confirm that every affected foreign key remains enabled and trusted.

### 7.4 SQL Graph objects

`NomGraph_Person` and `NomGraph_Nominated` are SQL Graph node/edge tables. After transfer, validate their `sys.tables.is_node` and `sys.tables.is_edge` flags and run a representative `MATCH` query. Do not infer graph health only from row counts.

### 7.5 Locking and downtime

`ALTER SCHEMA` takes schema-level locks. The cutover must happen in a maintenance window with the following writers and readers paused or drained:

- fraud analytics scheduled and manual executions
- integrity-check consumer
- integrity-check-extension consumer
- backend requests that use the integrity tables
- auxiliary-service reads
- maintenance scripts and external jobs

With multiple analytics replicas enabled, stopping only one replica is insufficient. Confirm that no tenant lease is active and no stage is running before starting the migration.

## 8. Deployment constraint

Schema migration, backend, analytics, integrity-check, integrity-check-extension, and auxiliary-service deployments are triggered by independent workflows. A single commit that both transfers the tables and changes all SQL references can race:

- If the schema migration finishes first, old containers still query `dbo` and fail.
- If a new container starts first, it queries `integrity` before the tables move and fails.

For the one-step cutover, these deployments must be orchestrated as one release. Prepare and test the migration and application images first, then enforce this order:

1. Stop and drain all affected consumers.
2. Apply and verify the database migration.
3. Activate the prepared application revisions.
4. Run controlled smoke tests.
5. Resume normal triggers and processing.

The implementation does not change the existing workflow triggers. The coordinated release process must ensure the database migration completes before the new application revisions become active, while preventing old revisions from using the migrated layout. Record the exact image digests and prior active revisions so rollback does not depend on rebuilding code.

## 9. Recommended rollout

### Phase 0: Confirm the inventory

- Run the database preflight queries against the sandbox database.
- Reconcile the returned objects with the proposed scope.
- Identify database modules and external clients.
- Confirm that `ApproverPairFlags` is present and include it in the captured metadata and row-count baseline.
- Record row counts, temporal metadata, graph flags, indexes, constraints, and permissions.
- Confirm the migration principal's effective permissions.

Exit criterion: the target list and all non-repository consumers have named owners.

### Phase 1: Prepare without activating

- Replace active `dbo` qualifiers for the in-scope objects with the permanent `integrity` schema.
- Centralize the permanent `[integrity]` constant in each database access layer where that improves consistency.
- Remove schema-selection configuration that exists only for this transition.
- Implement migration `0072`, including the downgrade and metadata assertions.
- Update Terraform, deployment coordination, scripts, tests, and active documentation.
- Add a source-control check that rejects new `dbo.<in-scope-object>` references outside historical migrations and explicitly exempted documentation.
- Run unit, integration, and migration tests in an isolated database with the final schema layout.
- Build and identify the exact application images, but do not activate them in the shared sandbox.
- Record the currently active application revisions and confirm the migration downgrade against a disposable database.

Exit criterion: the database migration, downgrade, and final-schema application images are tested and ready, while the shared sandbox still runs the unchanged `dbo` layout and old revisions.

### Phase 2: Coordinated sandbox cutover

- Start the coordinated cutover only after all migration and application artifacts are ready.
- Disable analytics triggers and pause/drain all consumers listed in section 7.5.
- Confirm that no analytics tenant lease or stage attempt remains active.
- Take a current backup or verify the platform restore point and rollback window.
- Execute the new migration to create `integrity`, transfer the objects, restore temporal versioning, and validate metadata.
- Activate the prepared application revisions that directly reference `integrity`.
- Start consumers in a controlled order.
- Run database and application verification.

Suggested activation order:

1. Backend in maintenance mode for targeted health checks
2. Integrity-check and integrity-check-extension with controlled test messages
3. Auxiliary service
4. One manually triggered analytics execution with one replica
5. Scheduled analytics and the configured replica count

Exit criterion: all acceptance criteria pass and the sandbox completes an observation period without `dbo` lookup errors, permission failures, temporal errors, or graph-query regressions.

### Phase 3: Resume and observe

- Re-enable the approved automatic deployment workflows.
- Resume scheduled analytics and message processing only after the controlled tests pass.
- Monitor database errors, integrity message failures, analytics stage attempts, and backend error rate.
- Keep the prior application revisions and migration downgrade available until the rollback observation period closes.

Exit criterion: the sandbox remains healthy through the agreed observation period.

### Phase 4: Cleanup

- Keep the permanent integrity schema constants and the source-control guard.
- Update support queries and operational runbooks.
- Archive the preflight and verification output with the deployment evidence.

If this migration is later promoted to an environment with real tenants, treat that as a separate change. Reassess downtime, backup/restore requirements, external consumers, and compatibility needs rather than assuming the sandbox risk decision automatically applies.

### Estimated effort

The current estimate is **8–13 hours of implementation and verification**, followed by a **45–90 minute coordinated sandbox cutover**. This is approximately one long working day or two normal working days, depending on test failures and database access.

| Work item | Estimate |
|---|---:|
| Final dependency inventory and migration preflight | 1–1.5 hours |
| Update database references across services, scripts, Terraform, prompts, and active documentation | 2.5–4 hours |
| Implement Alembic upgrade/downgrade, including temporal-table and permission handling | 2–3 hours |
| Update tests, run the relevant suites, and repair regressions | 2–3.5 hours |
| Prepare or adjust coordinated deployment sequencing and the cutover checklist | 0.5–1 hour |
| Execute and validate the sandbox cutover | 0.75–1.5 hours |

The estimate assumes:

- the proposed 14-object scope is complete;
- there are no unknown external consumers or database modules that must be rewritten;
- the existing test environments can create or restore a representative SQL Server database;
- required Azure and database access is available for the cutover; and
- no unrelated deployment failure blocks activation of the prepared revisions.

The largest uncertainty is not changing the SQL qualifiers; it is safely preserving and validating the temporal-table relationship, object permissions, SQL Graph behavior, and coordinated deployment order. Discovery of additional consumers or permissions could add several hours.

## 10. Migration `0072` outline

This is an implementation outline, not a copy-ready migration. The Alembic migration should use explicit statements, defensive checks, and transaction handling appropriate to the existing migration framework.

```sql
-- Create the schema if it does not exist. Execute CREATE SCHEMA in its own
-- dynamic batch where required by SQL Server batch rules.
IF SCHEMA_ID(N'integrity') IS NULL
    EXEC(N'CREATE SCHEMA [integrity] AUTHORIZATION [dbo]');

BEGIN TRANSACTION;

ALTER TABLE [dbo].[IntegrityComponentStatus]
    SET (SYSTEM_VERSIONING = OFF);

ALTER SCHEMA [integrity] TRANSFER [dbo].[IntegrityComponentStatus_History];
ALTER SCHEMA [integrity] TRANSFER [dbo].[IntegrityComponentStatus];

-- Transfer the remaining approved objects with one explicit statement each.
ALTER SCHEMA [integrity] TRANSFER [dbo].[GNN_UserEmbeddings];
-- ...

ALTER TABLE [integrity].[IntegrityComponentStatus]
    SET (SYSTEM_VERSIONING = ON (
        HISTORY_TABLE = [integrity].[IntegrityComponentStatus_History],
        DATA_CONSISTENCY_CHECK = ON,
        HISTORY_RETENTION_PERIOD = 24 MONTHS
    ));

-- Reapply approved object-specific permissions, if any.
-- Run metadata assertions before commit.

COMMIT TRANSACTION;
```

The migration should fail before changing state if:

- a required source object is absent or a destination object already exists;
- the temporal current/history relationship differs from the expected design;
- an unhandled database module depends on a target object by its old name;
- unexpected object-specific permissions exist;
- the migration identity lacks the necessary permissions; or
- an active application/job condition that the runbook requires to be stopped is detected through an available guard.

The downgrade should perform the reverse transfers, including the same temporal disable/transfer/re-enable procedure and permission restoration. A code rollback alone is not a database rollback.

## 11. Preflight queries

The following queries are starting points for the implementation runbook. Save their output before each environment's cutover.

### 11.1 Object metadata

```sql
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    t.object_id,
    t.temporal_type_desc,
    hs.name AS history_schema_name,
    ht.name AS history_table_name,
    t.history_retention_period,
    t.history_retention_period_unit_desc,
    t.is_node,
    t.is_edge
FROM sys.tables AS t
JOIN sys.schemas AS s ON s.schema_id = t.schema_id
LEFT JOIN sys.tables AS ht ON ht.object_id = t.history_table_id
LEFT JOIN sys.schemas AS hs ON hs.schema_id = ht.schema_id
WHERE t.name IN (
    N'GNN_UserEmbeddings', N'GNNScoringPolicies',
    N'GraphPatternFindings', N'GraphScoringChangeRequests',
    N'GraphScoringPatternParameters', N'GraphScoringPolicies',
    N'IntegrityComponentStatus', N'IntegrityComponentStatus_History',
    N'IntegrityDecisionResults', N'NomGraph_NominationEmbedding',
    N'NomGraph_Nominated', N'NomGraph_Person',
    N'UserGraphFlags', N'ApproverPairFlags'
)
ORDER BY s.name, t.name;
```

### 11.2 Database dependencies

```sql
SELECT
    OBJECT_SCHEMA_NAME(d.referencing_id) AS referencing_schema,
    OBJECT_NAME(d.referencing_id) AS referencing_object,
    o.type_desc AS referencing_type,
    d.referenced_schema_name,
    d.referenced_entity_name
FROM sys.sql_expression_dependencies AS d
JOIN sys.objects AS o ON o.object_id = d.referencing_id
WHERE d.referenced_id IN (
    OBJECT_ID(N'dbo.GNN_UserEmbeddings'),
    OBJECT_ID(N'dbo.GNNScoringPolicies'),
    OBJECT_ID(N'dbo.GraphPatternFindings'),
    OBJECT_ID(N'dbo.GraphScoringChangeRequests'),
    OBJECT_ID(N'dbo.GraphScoringPatternParameters'),
    OBJECT_ID(N'dbo.GraphScoringPolicies'),
    OBJECT_ID(N'dbo.IntegrityComponentStatus'),
    OBJECT_ID(N'dbo.IntegrityComponentStatus_History'),
    OBJECT_ID(N'dbo.IntegrityDecisionResults'),
    OBJECT_ID(N'dbo.NomGraph_NominationEmbedding'),
    OBJECT_ID(N'dbo.NomGraph_Nominated'),
    OBJECT_ID(N'dbo.NomGraph_Person'),
    OBJECT_ID(N'dbo.UserGraphFlags'),
    OBJECT_ID(N'dbo.ApproverPairFlags')
)
ORDER BY referencing_schema, referencing_object;
```

This metadata view does not discover every dynamic-SQL dependency. Also search module definitions for the table names and review scheduled jobs and external clients.

### 11.3 Object-specific permissions

```sql
SELECT
    s.name AS schema_name,
    o.name AS object_name,
    USER_NAME(p.grantee_principal_id) AS grantee,
    p.state_desc,
    p.permission_name
FROM sys.database_permissions AS p
JOIN sys.objects AS o
    ON o.object_id = p.major_id
JOIN sys.schemas AS s
    ON s.schema_id = o.schema_id
WHERE p.class_desc = N'OBJECT_OR_COLUMN'
  AND o.name IN (
      N'GNN_UserEmbeddings', N'GNNScoringPolicies',
      N'GraphPatternFindings', N'GraphScoringChangeRequests',
      N'GraphScoringPatternParameters', N'GraphScoringPolicies',
      N'IntegrityComponentStatus', N'IntegrityComponentStatus_History',
      N'IntegrityDecisionResults', N'NomGraph_NominationEmbedding',
      N'NomGraph_Nominated', N'NomGraph_Person',
      N'UserGraphFlags', N'ApproverPairFlags'
  )
ORDER BY s.name, o.name, grantee, p.permission_name;
```

### 11.4 Foreign keys

```sql
SELECT
    fk.name AS foreign_key_name,
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS parent_schema,
    OBJECT_NAME(fk.parent_object_id) AS parent_table,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS referenced_schema,
    OBJECT_NAME(fk.referenced_object_id) AS referenced_table,
    fk.is_disabled,
    fk.is_not_trusted
FROM sys.foreign_keys AS fk
WHERE OBJECT_NAME(fk.parent_object_id) IN (
          N'GNN_UserEmbeddings', N'GNNScoringPolicies',
          N'GraphPatternFindings', N'GraphScoringChangeRequests',
          N'GraphScoringPatternParameters', N'GraphScoringPolicies',
          N'IntegrityComponentStatus', N'IntegrityDecisionResults',
          N'NomGraph_NominationEmbedding', N'NomGraph_Nominated',
          N'NomGraph_Person', N'UserGraphFlags', N'ApproverPairFlags'
      )
   OR OBJECT_NAME(fk.referenced_object_id) IN (
          N'GNN_UserEmbeddings', N'GNNScoringPolicies',
          N'GraphPatternFindings', N'GraphScoringChangeRequests',
          N'GraphScoringPatternParameters', N'GraphScoringPolicies',
          N'IntegrityComponentStatus', N'IntegrityDecisionResults',
          N'NomGraph_NominationEmbedding', N'NomGraph_Nominated',
          N'NomGraph_Person', N'UserGraphFlags', N'ApproverPairFlags'
      )
ORDER BY foreign_key_name;
```

## 12. Post-migration verification

Database verification must show:

- every approved target exists in `integrity` and no target remains in `dbo`;
- row counts match the captured preflight values;
- indexes and constraints remain present and enabled;
- all affected foreign keys are enabled and trusted;
- approved permissions are present;
- `IntegrityComponentStatus` is system-versioned;
- its history table is `integrity.IntegrityComponentStatus_History`;
- its history retention remains 24 months;
- `NomGraph_Person.is_node = 1` and `NomGraph_Nominated.is_edge = 1`;
- a representative SQL Graph `MATCH` query succeeds; and
- database modules no longer contain active `dbo` references to the moved objects.

Application verification must include:

- backend health plus representative reads of decisions, component status, and graph findings;
- one controlled integrity-check message and one integrity-check-extension message;
- auxiliary-service integrity read paths;
- one analytics run for a single enabled tenant, confirming GRAPH, TABULAR, GNN, and FORECAST stage history;
- a subsequent analytics run with the intended replica count, confirming each tenant is leased once;
- tenant enable/disable behavior;
- no `Invalid object name 'dbo....'`, permission, temporal, or graph errors in telemetry; and
- no unexplained change in model inputs or outputs caused solely by schema qualification.

## 13. Rollback plan

Rollback is a maintenance operation, not merely a redeployment:

1. Pause and drain the same consumers used for cutover.
2. Stop any newly activated revisions so no code that references `integrity` is running.
3. Run the migration downgrade to transfer the objects back to `dbo`.
4. For the temporal pair, disable versioning, transfer both tables, and re-enable it with the explicit `dbo` history table and 24-month retention.
5. Restore object-specific permissions and validate graph/foreign-key metadata.
6. Reactivate the recorded pre-cutover application revisions that reference `dbo`.
7. Restart consumers and repeat the smoke tests before restoring normal triggers.

Define the maximum observation period during which this downgrade remains supported. Any later schema changes made only against `integrity` must either include a compatible downgrade or formally close the rollback window.

If migration `0072` fails before its transaction commits, keep all consumers stopped, verify that the database still has the complete `dbo` layout, and reactivate the old revisions. Do not activate the new revisions after a failed or partially verified migration.

## 14. Alternatives considered

### Coordinated one-step cutover

Selected for the current sandbox because there are no real tenants and the affected services can be stopped together. It produces the cleanest final state: application code directly references `integrity`, with no transient container configuration or database compatibility objects.

The independent workflow race remains real, so this option requires enforced sequencing and stopped consumers as described in sections 8 and 9.

### Two-release configurable schema

Not selected because `INTEGRITY_SQL_SCHEMA` would be a transitional, database-specific container setting that becomes confusing after the migration. It offers more deployment-order tolerance, but that benefit is not needed for the current sandbox risk profile.

### Database-driven schema detection

Not selected because it introduces temporary runtime branching and mixed-layout tests. It would be useful if old and new database layouts had to be supported simultaneously, which is not required for this sandbox cutover.

### Compatibility synonyms in `dbo`

Not selected. They would require proving every access pattern, including `MERGE`, temporal `FOR SYSTEM_TIME`, SQL Graph `MATCH`, and metadata/`OBJECT_ID` checks. They would also prolong the old namespace and could hide incomplete migration work.

### Views in `dbo`

Not suitable as a general compatibility layer because the tables include writes, temporal behavior, graph tables, and metadata-sensitive code.

## 15. Open decisions

- Which external reporting, notebook, or scheduled-job consumers exist?
- Will runtime access continue through `db_datareader`/`db_datawriter`, or should the migration introduce explicit schema-level roles such as `integrity_reader` and `integrity_writer`?
- What maintenance-window duration and rollback observation period are acceptable?
- What sandbox observation period is required after the cutover?
- Who will execute the migration with sufficient privileges, or apply and revoke the temporary schema permissions for the migration job?

## 16. Authoritative references

- [ALTER SCHEMA (Transact-SQL)](https://learn.microsoft.com/en-us/sql/t-sql/statements/alter-schema-transact-sql?view=sql-server-ver17)
- [Database-level roles](https://learn.microsoft.com/en-us/sql/relational-databases/security/authentication-access/database-level-roles?view=sql-server-ver17)
- [Reserved keywords (Transact-SQL)](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/reserved-keywords-transact-sql?view=sql-server-ver15)
- [Change the schema of a system-versioned temporal table](https://learn.microsoft.com/en-us/sql/relational-databases/tables/temporal/change-schema?view=sql-server-ver17)
- [Stop system-versioning on a system-versioned temporal table](https://learn.microsoft.com/en-us/sql/relational-databases/tables/temporal/stop-system-versioning?view=sql-server-ver17)
