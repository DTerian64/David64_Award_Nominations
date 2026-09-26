# Integrity engine history windows

The operational windows live in each tenant's `dbo.Tenants.integrity_config`:

```json
{
  "graph_pattern": {
    "detection_window_days": 180,
    "detector_windows": {
      "Ring": 60,
      "BipartiteDenseBlock": 180,
      "TemporalBurst": 180,
      "SuperNominator": 180,
      "SuperBeneficiary": 180,
      "CopyPasteFraud": 180,
      "HiddenCandidate": 180,
      "LowRecognitionNominator": 180
    }
  },
  "gnn": { "window_days": 365 },
  "tabular": { "window_days": 365 }
}
```

This is a fragment: preserve the existing score-routing and other settings.
The shared Graph value is the fallback for an omitted detector entry. It is not
a second filter after that detector's own window. There is no new policy table.

## Graph Analytics

- Migration **0069** sets Ring to **60 days** and the other windowed detectors to
  **180 days for every tenant**, building on migrations 0067/0068.
- The job loads the maximum required history once, then gives each detector its
  own population. Nomination Desert continues to use all-time participation.
- Each analytics run reads the tenant JSON afresh and records the effective
  windows in its diagnostics and immutable inference snapshot.
- Live nomination checks use that published snapshot's window. They also exclude
  nominations that have aged out at the candidate's timestamp. An existing
  365-day snapshot is not relabelled as 180 days; the next successful Graph run
  must publish the replacement.
- Embedding cleanup uses the longest resolved Graph detector window required by
  active tenants, including overrides longer than the fallback.
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

1. Deploy schema migration 0069, the core, analytics job, integrity-check, backend
   and frontend.
2. Run fraud-analytics-job; no corpus reset or reseeding is required.
3. Verify Graph diagnostics and the new snapshot carry `detector_windows` with
   Ring 60 and other patterns 180. `window_days: 180` is the maximum history loaded,
   not the Ring window. Engine Status distinguishes configured/serving windows.
4. For Tenant 5, verify GNN diagnostics still report `window_days: 365`.

The smaller Graph window reduces the eligible graph and ring workload. It is
not a guarantee that the job finishes within two hours; SQL finding persistence
may still need separate optimization.

## Tabular models: Random Forest and Tabular MLP

- Migration **0068** adds `tabular.window_days: 365` for every tenant lacking this
  setting, preserves an existing tabular value, and leaves Graph/GNN untouched.
- Both candidates fit/evaluate nominations in that trailing window. The adapter
  loads one additional window as warm-up context; older context is not a training
  target. Every target's history is restricted to its own prior window, with an
  inclusive lower boundary and strict `(NominationDate, NominationId)` ordering.
  The current nomination and all future nominations are excluded.
- Counts, pair/reciprocal history, uniqueness, amount means/population standard
  deviations and amount z-scores use this causal history, not full-corpus aggregates.
  Population eligibility is unchanged: pending HRBP review and description-engine
  rejections are excluded; other HRBP-rejected nominations remain eligible.
- Semantic features now match live behavior: compare the target description with
  the beneficiary's latest 20 prior **authored** descriptions within the same
  window. Previously training used full-corpus descriptions of awards received.
- Category target encoding uses chronological, forward-only out-of-fold rates:
  each block sees only labels known before its start. Holdout uses training labels
  known before holdout starts. The contract is
  `category-fraud-rate-forward-oof-v1`; both candidates keep permutation importance
  on the same held-out set. Published models must be retrained to adopt it.
- The column set is unchanged. The feature schema is `tabular-v2` and artifact
  versions start `tabular-v2-` because historical feature semantics changed.
  Payloads/manifests record `history_window_days` and `history_feature_contract`.
  Live checks use the model's recorded window, not subsequently edited settings.
  Existing v1 models keep their original full-history transform until replaced.

## Setup and Engine Status

- Integrity Setup → Model Setup → Scoring & Routing shows all three editable windows
  together, plus a per-pattern Graph table. Data scientists see these read-only.
- Engine Status shows **Configured history window** and **Serving history window**
  on each engine card, including engines which have not run. The latter comes
  from the successful publication corresponding to the serving version, not a
  newer failed/skipped attempt. If retained history has no evidence, the UI says
  it is not recorded; it never substitutes the configured value.
- A differing configured value is explicitly pending the next successful Graph
  snapshot or GNN/Tabular model publication. Legacy tabular artifacts are labelled
  **Full history (legacy model)**. Nomination Desert remains all-time analytics.
- GNN v4 diagnostics use two separate tables: a final-test metric comparison
  (selected GNN, raw-feature MLP, engineered-graph MLP), and pattern-head states
  (state, training/final-test pattern-positive counts, final-test PR-AUC).
  PR-AUC/ROC-AUC are on a 0–1 scale, base rate is a percentage, and lift is a
  multiplier. These describe the latest training attempt, not automatically
  the serving model; the existing admission and publication logic is unchanged.
- Deploy migration 0068, backend/frontend, integrity-check, and fraud-analytics-job,
  then rerun training. No corpus reset is required. Deploy the new worker before
  publishing v2 tabular artifacts so the consumer understands their contract.
