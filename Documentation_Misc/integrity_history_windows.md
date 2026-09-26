# Graph and GNN history windows

The operational windows live in each tenant's `dbo.Tenants.integrity_config`:

```json
{
  "graph_pattern": { "detection_window_days": 180 },
  "gnn": { "window_days": 365 }
}
```

This is a fragment: preserve the existing score-routing and other settings.
There is no per-detector override map and no new policy table.

## Graph Analytics

- Migration **0067** sets the shared Graph window to **180 days for every tenant**.
- All window-based detectors use the same nomination population. Nomination
  Desert continues to use all-time participation.
- Each analytics run reads the tenant JSON afresh and records the effective
  window in its diagnostics and immutable inference snapshot.
- Live nomination checks use that published snapshot's window. They also exclude
  nominations that have aged out at the candidate's timestamp. An existing
  365-day snapshot is not relabelled as 180 days; the next successful Graph run
  must publish the replacement.
- Embedding cleanup uses the longest Graph window required by active tenants.
- `GraphScoringPolicies.DetectionWindowDays` remains for historical records and
  draft editing, but is not the operational source. The active policy UI reads
  the tenant window. Publishing a draft writes its staged window into the tenant
  JSON, preserving all other configuration namespaces.
- The old container `DETECTION_WINDOW_DAYS` setting no longer controls Graph;
  updating the tenant JSON does not require Terraform or a container restart.

## GNN

- GNN uses the separate `gnn.window_days` entry. It does not inherit Graph's window.
- Migration 0067 copies each tenant's current active GNN training window into
  this entry; it preserves an already configured tenant GNN window. Tenant 5
  therefore retains **365 days**. Tenants without a GNN policy are not assigned
  a new GNN window by the migration.
- The configured GNN window continues to govern both training history and causal
  context; this change does not introduce separate context or pattern-head windows.
- Training reads this entry afresh. Live inference continues to reconstruct
  inputs using `causal_context_window_days` in the trained artifact. Changing
  tenant configuration affects the next training run, not an existing model's inputs.
- GNN policy drafts stage window edits; publishing copies the staged value to the
  tenant JSON. Other model hyperparameters remain in `GNNScoringPolicies`.

## Rollout

1. Deploy schema migration 0067, the analytics job, integrity-check, and backend.
2. Run fraud-analytics-job; no corpus reset or reseeding is required.
3. Verify the Graph console reports `detection window: 180 days`, and Graph
   diagnostics report `window_days: 180`. The new inference snapshot must also
   carry `window_days: 180`.
4. For Tenant 5, verify GNN diagnostics still report `window_days: 365`.

The smaller Graph window reduces the eligible graph and ring workload. It is
not a guarantee that the job finishes within two hours; SQL finding persistence
may still need separate optimization.

## Tabular models (unchanged)

Random Forest and Tabular MLP currently share a full-history training dataset
(`SourceReadRequest.window_days=None`). Live tabular feature counts also use
unbounded history. Description comparisons use a count limit rather than a day
window. No tabular window is introduced by this change; doing so would require
matching feature-history changes in training and live inference, then retraining.
