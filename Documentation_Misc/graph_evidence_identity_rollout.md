# Graph evidence identity and synthetic reset (0056)

This replaces the per-run archive introduced in 0055. Do not edit an already
applied migration: deploy 0056 and the matching services together.

## Corrected contract

- Identity is SHA-256 of tenant, detector type, sorted unique affected-user IDs,
  and sorted unique nomination IDs. It does **not** include scoring policy or run.
- The database enforces uniqueness on `(TenantId, FindingHash)` again. The job
  compares this key inside a locked `MERGE`: existing findings are updated;
  only previously unseen evidence is inserted. FindingId remains stable after
  the one-time reset.
- Existing findings are still rescored on every run. Score, severity, policy,
  score components, `RunId`, and `DetectedAt` reflect their latest assessment.
  A change to evidence creates a different hash, not a duplicate of that evidence.
- `UserGraphFlags.FindingsJson` remains the complete current affected-user
  snapshot, built from **all** detected findings, not only newly inserted ones.
- Both stores and the serving marker publish in one transaction. Snapshot
  schema version is now 2, preventing old per-run metadata from being advertised
  as current by the updated services.
- The Analytics current run is complete, including zero findings. Older groups
  contain only findings last assessed in those runs; they are **not** immutable
  historical snapshots. A repeated finding moves to its latest run without
  another row. `SnapshotComplete` on stored rows is retained for schema
  compatibility but is no longer an authority; current completeness comes from
  the serving marker and matching count. No run-history table is added.
- Ring detection, overlap handling, detector formulas, RF, GNN, and nomination
  routing rules are unchanged.
- Decision Engines labels the Graph diagnostic **Last Successful Run Finding
  Count**, with the tooltip: "Distinct findings detected in the last successful
  Graph Analytics run, including previously known findings." The calculation
  and stored `finding_count` field remain unchanged.

## Destructive reset boundary

The user approved clearing existing synthetic Graph data for tenants **1, 2, 3**:

- Delete their rows from `dbo.GraphPatternFindings` and `dbo.UserGraphFlags`.
- Mark their GRAPH component unavailable and clear its serving snapshot metadata.
- Keep both tables, their identities, nomination records, users, model policies,
  `IntegrityDecisionResults`, and `Nomination_Logs`. Existing nomination decisions
  and logs are not recalculated. Old finding links stop resolving; identity is
  deliberately not reseeded so old links cannot point to unrelated new findings.

Source inspection found finding-ID readers in the API detail/export paths and
the Graph assistant tool, not nomination-decision writers. The migration checks
actual database foreign keys and enabled table triggers before deleting data and
refuses to continue if any exist. It also refuses Graph data outside tenants
1–3. External references/dynamic SQL require the deployment operator's inventory
check; those cannot be proven absent from repository inspection.

## Deployment order

1. Confirm the target is the approved synthetic database. Pause scheduled/manual
   fraud-analytics Graph jobs and integrity-check, and drain in-flight executions.
   Avoid Graph policy edits during cutover. Back up/export Graph data if recovery
   is needed; deleted history has no automatic undo.
2. Apply the Terraform migration-job configuration. The shared module supplies
   permanent `AWARD_DATABASE_NAME` from each environment's `sql_database_name`,
   alongside `SQL_DATABASE` for existing connection consumers. Migration 0056
   checks it against the connected database (`SELECT DB_NAME()`).
   Database identity is not destructive authorization: the sandbox module also
   temporarily sets `graph_findings_reset_approved = true`, supplying
   `GRAPH_FINDINGS_RESET_APPROVED=true`. Other environments default to no reset
   approval. Empty fresh installations need no reset opt-in. Do not bypass
   reported reference/trigger blockers.
3. Apply migration **0056** through the normal migration deployment. It performs
   the scoped reset and restores cross-run uniqueness transactionally. Remove
   the sandbox `graph_findings_reset_approved` override afterward and apply
   Terraform again. Keep `AWARD_DATABASE_NAME` for ongoing use.
4. Deploy the matching backend, fraud-analytics-job, integrity-check, and frontend.
   Do not resume an old Graph writer against the corrected schema.
5. Run Graph Analytics for tenants 1, 2, 3; verify availability, schema version 2,
   current-run counts, all eight detector entries, and six scoring detectors.
6. Rerun against unchanged input/policy. Finding IDs and total stored rows must
   remain unchanged; the serving run and latest assessment fields should advance.
   Verify current user evidence still contains existing-hash findings.
7. Verify a policy-only change on a controlled test preserves hashes/IDs while
   updating scores/policy metadata. Changed nomination evidence may legitimately
   produce new hashes. Resume regular processing after checks succeed.

## Verification queries (read-only)

```sql
SELECT TenantId, FindingHash, COUNT_BIG(*) AS Copies
FROM dbo.GraphPatternFindings
GROUP BY TenantId, FindingHash
HAVING COUNT_BIG(*) > 1; -- must be empty

SELECT TenantId, PatternType, COUNT_BIG(*) AS UniqueFindings
FROM dbo.GraphPatternFindings
GROUP BY TenantId, PatternType;

SELECT s.TenantId, s.ServingStatus, s.RunId,
       JSON_VALUE(s.DiagnosticsJson, '$.snapshot_schema_version') AS SnapshotSchema,
       JSON_VALUE(s.DiagnosticsJson, '$.finding_count') AS ReportedFindings,
       (SELECT COUNT_BIG(*) FROM dbo.GraphPatternFindings f
        WHERE f.TenantId=s.TenantId AND f.RunId=s.RunId) AS CurrentFindings
FROM dbo.IntegrityComponentStatus s
WHERE s.Component='GRAPH' AND s.TenantId IN (1,2,3);
```

Distinct overlapping rings can still greatly outnumber nominations. This change
removes repeated copies across runs and policies, not overlapping Ring findings.
No live reset, deployment, or recalculation is performed by editing these files.
