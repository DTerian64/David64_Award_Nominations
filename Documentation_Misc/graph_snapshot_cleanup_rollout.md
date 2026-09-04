# Graph snapshot cleanup (migration 0055)

**Superseded for finding storage and rollout by [migration 0056](graph_evidence_identity_rollout.md).**
Do not use the per-run archive design below for new deployments. Findings now
have policy-independent hashes and are refreshed in place across runs.

## Scope

- Publish a new version of each active Graph scoring policy with Super Beneficiary enabled for scoring. Preserve its beneficiary role, formula, parameters, and thresholds. Update editable drafts to match.
- Keep Hidden Candidate and Desert analytics-only. No Ring detection, overlap suppression, RF, or GNN changes.
- Make `FindingsJson` the sole per-user Graph evidence, retaining tenant/user/date keys and `LastUpdatedUtc`. Remove the eight obsolete summary columns from `UserGraphFlags`.
- Archive every finding in each successful run. Deduplicate by tenant/run/hash, not across runs. Publish findings, affected-user snapshots, and the serving marker atomically.
- Show all eight detectors, policy version, run identity, and complete/legacy status in Graph Analytics. Group nomination-log evidence without removing findings or adding their scores together.

## Coordinated deployment

This change is not compatible with running old and new writers against the same schema. Do not roll services independently while processing nominations.

1. Pause scheduled/manual Graph jobs and integrity-check processing. Drain any in-flight work. Prevent Graph policy edits during cutover. Take the normal database recovery checkpoint/backup.
2. Apply migration **0055** using the existing schema-migration deployment process. It checks database expressions, indexes, and foreign keys for dependencies before dropping summary columns. If it reports a dependency, stop and review that consumer; do not bypass the guard. Dynamic SQL and external consumers still require an operational inventory check.
3. Deploy the matching backend, fraud-analytics-job, integrity-check, and frontend changes. The migration marks Graph unavailable until refreshed; no missing evidence should be interpreted as a clean Graph assessment.
4. Run Graph Analytics for each tenant. It must use the newly published policy. The job commits the archive, affected-user evidence, and serving marker together.
5. Verify the checks below, then resume inference and the regular job schedule. Only administrators may edit policy parameters; Data Scientists retain read-only inspection/request access.

## Acceptance checks

- Each tenant's new active policy has six scoring detectors and two analytics-only detectors (unless an administrator deliberately disabled a detector).
- Graph Analytics selects the current complete run and shows eight detector chips, including zero-count/disabled detectors. Its total equals the serving marker's `finding_count`, and its policy version agrees with that marker.
- For an affected user, `FindingsJson` contains the current `snapshot_run_id` and `scoring_policy_version`. Missing/malformed/mismatched JSON on an existing affected-user row produces `INVALID_SNAPSHOT`, not a clean score.
- A user absent from a valid, published snapshot has no findings. A successful zero-finding run is visible as the current run and clears prior findings from the inference view.
- A failed refresh leaves the previous serving run and diagnostics intact; latest-attempt status still reports failure.
- Nomination logs show the winning finding first, then expandable detector groups and individual evidence. The nomination score remains the highest relevant finding, not a sum. Export and detail views remain available for historical findings.

## History, storage, and recovery

Existing `GraphPatternFindings` rows are retained and marked **legacy partial**, because the old writer saved only previously unseen hashes. Their missing findings cannot be reconstructed by this migration. New runs containing findings are complete archives, including repeat findings from earlier runs; expect increased storage and review retention separately.

No run-history table is added. The existing component-status marker exposes the **current** successful zero-finding run. After a later successful run replaces that marker, an older zero-finding run has no finding rows and is no longer listed. Its permanent history would require a separate run ledger or another agreed archive design.

Migration 0055 has no destructive downgrade: discarded summaries and the old cross-run uniqueness contract cannot be restored faithfully. On cutover failure, keep processing paused and repair forward or use the approved database recovery process with matching old service versions. Do not restart old writers on the new schema.

New-tenant provisioning must supply an eight-detector active policy with Super Beneficiary enabled for routing; this migration updates tenants present when it runs, not policies created later by external provisioning scripts.
