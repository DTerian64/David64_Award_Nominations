# GNN v2 Training Strategy

**Status:** Operational candidate selection implemented; deployment evaluation pending  
**Owner:** Integrity modeling  
**Applies to:** `fraud-analytics-job`, `dbo.IntegrityDecisionResults`, `dbo.GNN_UserEmbeddings`, GNN artifact storage  
**Last updated:** 2026-09-11

## 1. Purpose

This document defines how GNN v2 is trained, evaluated, compared with alternative
architectures, packaged, selected, and atomically activated for live integrity scoring.
It is the operational companion to
`Documentation_Misc/gnn_v2_and_integrity_check_extension_design.md`.

The strategy preserves the ELCE premise that GNN is an independent decision
engine. GNN learns from raw, time-valid nomination topology and explicit human
outcomes. It does not learn from Random Forest, Graph Analytics, semantic-check,
composite-score, or routing outputs.

Every standard training run compares heterogeneous GraphSAGE, GCN-family, and
GATv2 architectures and selects one winner for the single GNN engine. A no-graph
MLP is always evaluated as the admission baseline. Candidate architectures are
not additional votes in the integrity decision.

The training job implements the rolling-fold candidate bake-off, deterministic
winner selection, post-selection refit, versioned candidate/serving artifacts,
winner-specific embeddings, and final-step activation through
`dbo.IntegrityComponentStatus.ServingVersion`.

## 2. Training principles

The following rules are mandatory:

1. **Tenant isolation:** one tenant produces one graph, one label population, one
   model version, and one artifact bundle. Cross-tenant references fail the run.
2. **Model independence:** inputs may contain raw business facts, but never the
   outputs of another integrity engine.
3. **Human-only supervision:** only explicit `FRAUD` and `LEGITIMATE` HRBP
   dispositions may enter the loss function.
4. **Temporal integrity:** a target nomination is never a node in the graph used
   to score that target.
5. **Out-of-time evaluation:** the newest interval is evaluated only after model
   fitting and is never used for early stopping or hyperparameter selection.
6. **Standard model selection:** every training run evaluates the approved
   candidates against the same tenant, labels, folds, feature availability,
   training budget, and selection rule.
7. **One live winner:** only the selected graph architecture is refitted and
   atomically activated as the tenant's GNN engine.
8. **Reproducibility:** the model, preprocessing state, graph snapshot, mappings,
   metrics, and artifact hashes travel as one immutable versioned bundle.

## 3. Data and label contract

### 3.1 Source population

The graph is built directly from `dbo.Nominations` and `dbo.Users` for one
tenant. It does not read `dbo.UserGraphFlags` or Graph Analytics findings.

The default lookback is 180 days, controlled by the active tenant row in
`dbo.GNNScoringPolicies`. The ordinary
behavior graph contains nominations whose current status is:

- `Pending`;
- `Approved`; or
- `Paid`.

An HRBP-reviewed rejected nomination may remain a supervised target, but it is
not inserted into later message-passing history as ordinary accepted behavior.

### 3.2 Canonical labels

`dbo.IntegrityDecisionResults.TrainingDisposition` is the model-neutral label
contract:

| TrainingDisposition | GNN target | Treatment |
|---|---:|---|
| `FRAUD` | 1 | Eligible human-confirmed target |
| `LEGITIMATE` | 0 | Eligible human-confirmed target |
| `EXCLUDED` | — | Never used as a target |
| `NULL` | — | Unlabeled; never used as a target |

Semantic-check routing never creates a GNN training label. RF bootstrapping and
pseudo-labels are RF-only and are never shared with GNN.

### 3.3 Outcome maturity

The current implementation consumes final HRBP dispositions available when the
weekly job runs. Before first live activation, the evaluation report must define
and verify an outcome-maturity delay so recently reviewed or correctable outcomes
cannot make a historical backtest appear more timely than it would have been.
The selected maturity policy must be written into the artifact manifest.

## 4. GNN v2 feature and topology baseline

The baseline feature schema is `gnn-v2`.

### 4.1 Node types

- `user`
- `nomination`
- `category`

### 4.2 Relations

- `user -> nominates -> nomination`
- `nomination -> benefits -> user`
- `nomination -> belongs_to -> category`
- reverse relation for every relation above

### 4.3 User attributes

- `LogNominationsMade`
- `LogNominationsReceived`
- `LogUniqueCounterparties`

### 4.4 Nomination attributes

- `LogAmount`
- `CategoryRelativeAmountRobustZScore`
- `DaysBeforeGraphCutoff`
- `DayOfWeekSin`
- `DayOfWeekCos`
- `MonthSin`
- `MonthCos`
- `HistoricalStatus`

Scalers and category-relative amount statistics are fitted only on the graph
history available to the fold. Target status and target recency are zeroed so a
later workflow outcome cannot leak into the target feature vector.

The feature definitions and order are persisted with every artifact. Changing
them requires a new feature-schema version.

## 5. Rolling-origin construction

### 5.1 Default partition

The default `RollingFoldCount=3` policy divides the observed timeline into five chronological
segments:

```text
Time ──────────────────────────────────────────────────────────────>
       S0          S1          S2          S3          S4

Fold 1 graph=S0              train=S1     next interval=S2
Fold 2 graph=S0+S1           train=S2     next interval=S3
Fold 3 graph=S0+S1+S2        train=S3     final holdout=S4
```

For every fold:

- graph history ends before its training targets;
- training targets do not appear as graph nodes;
- the training interval does not overlap another training interval; and
- graph-derived attributes are refitted from that fold's historical graph.

The final exclusive boundary is one day after the newest observed nomination,
so nominations on the latest date are included rather than silently dropped.

### 5.2 How rolling training uses the segments

The deployed candidate is optimized across the three disjoint training target
sets (`S1`, `S2`, and `S3`). During every epoch, each target set is scored against
its own time-valid graph snapshot and the normalized fold losses are accumulated
before the optimizer step.

The earlier “next intervals” are useful for historical experimentation, but
they are not reported as independent holdouts after they later become training
segments. Only `S4` remains untouched and supplies the reported out-of-time
evaluation metrics.

This approach recovers older human labels that the former 60/20/20 split left
inside graph history, without inserting a target into its own message-passing
context.

### 5.3 Fixed-epoch policy

Training uses a fixed epoch count, defaulting to 300. There is no early stopping
on the final holdout. Selecting an epoch from holdout PR-AUC would make the
holdout validation data and render the reported metric optimistic.

Current common defaults are:

| Parameter | Default |
|---|---:|
| Hidden dimension | 64 |
| User embedding dimension | 64 |
| Encoder layers | 2 |
| Epochs | 300 |
| Optimizer | Adam |
| Learning rate | 0.01 |
| Weight decay | 0.0005 |
| Random seed | 42 |
| Decoder hidden layers | 64, 32 |
| Decoder dropout | 0.2 |

Class imbalance is handled with a positive-class weight calculated from the
combined rolling training population.

## 6. Training gates

A tenant is skipped instead of producing a weak or undefined model when any
gate fails.

| Gate | Default | Failure reason |
|---|---:|---|
| Eligible behavior nominations | 300 | `BELOW_MINIMUM_VOLUME` |
| Tenant users | 50 | `BELOW_MINIMUM_VOLUME` |
| Distinct dates | `fold_count + 2` | `INSUFFICIENT_TEMPORAL_COVERAGE` |
| Human-confirmed labels | At least one | `NO_HUMAN_CONFIRMED_LABELS` |
| Fraud labels in combined rolling train population | 10 | `INSUFFICIENT_FRAUD_LABELS` |
| Fraud labels in final holdout | 10 | `INSUFFICIENT_FRAUD_LABELS` |
| Legitimate labels in combined rolling train population | At least one | `MISSING_LABEL_CLASS` |
| Legitimate labels in final holdout | At least one | `MISSING_LABEL_CLASS` |

The GNN component status records the observed positive and negative counts,
fold count, per-fold labeled training counts, configured minimums, and a stable
skip reason. A skipped run is not represented as a valid zero-risk model.

Before live selection, minimum legitimate counts should be made explicit rather than
remaining at the current “at least one” safeguard. This is especially important
because the known tenant label population is strongly fraud-heavy.

## 7. Standard candidate selection

This is the same operational pattern used by Forecasting: evaluate a fixed set
of models under one backtest contract, record all results, select the best
eligible model, and use that model for forward inference. Forecasting minimizes
MASE; GNN v2 maximizes holdout PR-AUC subject to graph-value and operational
guardrails.

### 7.1 Candidates

| Candidate | Purpose |
|---|---|
| `mlp` | Mandatory no-message-passing admission baseline; never served as GNN |
| `graphsage` | Eligible graph architecture |
| `gcn` | Eligible GCN-family architecture implemented with PyG `GraphConv` |
| `gatv2` | Eligible attention-based graph architecture |

PyG `GCNConv` does not support the bipartite heterogeneous relations used here;
`GraphConv` is therefore used for the GCN-family benchmark.

TGN is deliberately excluded from the initial candidate set because it
introduces event memory and a different serving lifecycle. It may join a future
candidate set only after that lifecycle is implemented. It would still compete
for the same single GNN engine.

### 7.2 Fair-comparison rules

Every standard training run evaluates every configured candidate using:

- the same tenant data;
- the same human-confirmed labels;
- the same rolling training folds;
- the same untouched final holdout;
- the same fixed epoch budget;
- the same seed; and
- the same available non-topological attributes.

The comparison records each candidate's:

- out-of-time PR-AUC;
- ROC-AUC;
- holdout base rate and lift;
- Brier score;
- train and holdout counts;
- positive counts;
- parameter count;
- training duration; and
- holdout inference duration.

### 7.3 Selection rule

The architecture-selection policy is versioned. Its initial primary metric is
out-of-time holdout PR-AUC because fraud labels are imbalanced. Eligible graph
architectures are ranked by that metric, subject to all of the following:

- the graph architecture must exceed the MLP baseline by the configured minimum;
- both train and holdout class gates must pass;
- calibration, latency, memory, and artifact validation guardrails must pass;
- a candidate that fails training or artifact verification is ineligible; and
- an effectively tied challenger does not displace the incumbent architecture.

The tie tolerance and minimum improvement over MLP are policy settings recorded
with the run. The selection reason uses a stable code such as:

- `HIGHEST_ELIGIBLE_PR_AUC`;
- `INCUMBENT_RETAINED_WITHIN_TOLERANCE`;
- `NO_GRAPH_VALUE_OVER_MLP`;
- `INSUFFICIENT_ELIGIBLE_CANDIDATES`; or
- `CANDIDATE_GUARDRAIL_FAILED`.

If no graph architecture demonstrates sufficient value over the MLP, the MLP
does not become the GNN. The previous valid GNN remains active, or GNN remains
unavailable when no previous winner exists.

### 7.4 Winner refit

After selection, only the winning graph architecture is refitted using all
matured labels that are eligible at run time. The winner is then evaluated for
artifact and score reproducibility and activated as one version-matched serving
bundle. The pre-refit holdout comparison remains the selection evidence; it is
not relabeled as an unbiased metric for the refitted model.

## 8. Operational controls and configuration

All tenant-specific GNN behavior is versioned in `dbo.GNNScoringPolicies`.
Terraform owns only infrastructure such as the job image, compute, schedule,
storage connectivity, and SQL connectivity. It does not own model policy.

Policy identity, lifecycle, enablement, explanation controls, and audit data
remain typed columns. Evolving model behavior is held in the versioned
`ConfigurationJson` document:

```json
{
  "schema_version": 1,
  "model": {
    "hidden_dimension": 64,
    "embedding_dimension": 64
  },
  "training": {
    "epochs": 300,
    "rolling_fold_count": 3,
    "window_days": 180,
    "minimum_training_samples": 300,
    "minimum_users": 50,
    "minimum_positive_labels_per_split": 10
  },
  "artifacts": {
    "embedding_retention_days": 90,
    "stale_embedding_days": 14
  },
  "architecture_selection": {
    "candidate_architectures": ["graphsage", "gcn", "gatv2"],
    "selection_metric": "holdout_pr_auc",
    "minimum_improvement_over_mlp": 0.02,
    "incumbent_tie_tolerance": 0.01,
    "minimum_eligible_graph_candidates": 2
  },
  "score_routing": {
    "low_threshold": 25,
    "medium_threshold": 45,
    "high_threshold": 65,
    "critical_threshold": 85
  }
}
```

`ConfigurationJson` has an `ISJSON` database constraint. The backend validates
its schema version, supported architectures, numeric bounds, retention rules,
and threshold ordering before a draft can be saved or published. Candidate
architectures are a native JSON array rather than a nested encoded string.

The table uses the same lifecycle as Graph Analytics: one `ACTIVE` row, at
most one editable `DRAFT`, and retained `RETIRED` versions per tenant. Publishing
a draft is atomic. Serving controls apply to the next nomination handled;
training controls apply when the analytics job next begins that tenant. Every
training run records the policy ID, version, and complete policy snapshot in
its diagnostics and immutable artifact manifest.

Training and inference controls remain intentionally separate. Turning off
`TrainingEnabled` preserves the last valid serving winner. Turning off
`InferenceEnabled` excludes GNN from live scoring and reports
`DISABLED_BY_POLICY`; it never converts unavailability into a legitimate zero
score.

Inference is disabled only for exceptional conditions such as tenant isolation,
artifact integrity or version compatibility failures, severe score drift,
unacceptable latency/resource impact, a security incident, or a tenant-specific
policy suspension. Insufficient labels or one failed retraining attempt merely
preserves the incumbent winner.

## 9. Candidate and winner artifacts

Every successful selection run creates an immutable tenant-specific bundle:

```text
gnn/tenant_<tenant_id>/<model_version>/
  graph_snapshot.pt
  manifest.json
  candidates/
    mlp/
      decoder.pt
      metrics.json
    graphsage/
      encoder.pt
      decoder.pt
      metrics.json
    gcn/
      encoder.pt
      decoder.pt
      metrics.json
    gatv2/
      encoder.pt
      decoder.pt
      metrics.json
  serving/
    encoder.pt
    decoder.pt
```

Model versions and graph snapshot IDs include the tenant, run date, and a run-ID
suffix. The decoder and snapshot identify the same feature schema and graph
snapshot.

The manifest contains the rolling fold boundaries and counts, every candidate's
metrics and eligibility, the selected architecture and reason, feature order,
topology, architecture parameters, artifact hashes, and snapshot identity. The
snapshot contains tensor-only node attributes, edge
indexes, tenant-scoped ID mappings, fitted scalers, and category statistics and
is verified with restricted `weights_only=True` deserialization.

Candidate files preserve the weights used for the head-to-head comparison. The
`serving` files contain the selected architecture after its full matured-label
refit. The manifest links both sets and records the selected architecture for
the serving files. `integrity-check` loads only the versioned `serving` decoder.

## 10. Selection persistence

Selection is recorded in three places with distinct responsibilities. This is
intentional audit duplication, not three competing sources of truth.

### 10.1 Immutable run record

The versioned `manifest.json` is authoritative for why a candidate won and must
contain:

```json
{
  "model_version": "gnn-v2-20260911-t1-ab12cd34",
  "training_policy": {
    "policy_id": 12,
    "policy_version": 3
  },
  "selection": {
    "policy_version": "gnn-architecture-selection-v1",
    "selected_architecture": "graphsage",
    "selection_metric": "holdout_pr_auc",
    "selected_metric_value": 0.73,
    "mlp_baseline_value": 0.61,
    "improvement_over_mlp": 0.12,
    "selection_reason": "HIGHEST_ELIGIBLE_PR_AUC",
    "incumbent_architecture": "gcn",
    "incumbent_retained": false
  }
}
```

The manifest also holds the complete candidate metrics, eligibility decisions,
guardrail failures, artifact hashes, and refit metadata.

### 10.2 Current serving record

The current GNN row in `dbo.IntegrityComponentStatus` identifies what is active:

- `ServingVersion` stores the selected `model_version`;
- `ServingAsOf` stores the activation time;
- `RunId` identifies the selection run; and
- `DiagnosticsJson.selection` stores the selection summary and candidate metrics
  required by the Detection Engines UI.

The table is system-versioned, so earlier winners remain available through
`dbo.IntegrityComponentStatus_History`. No new selection table is required.

### 10.3 Nomination-level scoring record

Every `dbo.IntegrityDecisionResults.GnnResultJson` records the architecture and
version that actually scored that nomination:

```json
{
  "available": true,
  "architecture": "graphsage",
  "model_version": "gnn-v2-20260911-t1-ab12cd34",
  "training_policy_version": 3,
  "scoring_policy_version": 4,
  "graph_snapshot_id": "gnn-graph-v2-20260911-t1-ab12cd34",
  "probability": 0.73,
  "score": 73,
  "risk_level": "HIGH"
}
```

`dbo.GNNScoringPolicies` stores versioned training, selection, routing, and
explanation policy, never the dynamically selected winner. The training policy
version may differ from the scoring policy version: a newly published routing
threshold can apply immediately to the existing artifact, while architecture
and training changes apply only after the next successful training run.

### 10.4 Detection Engines UI

The GNN card in Detection Engines shows both the current serving winner and the
latest selection attempt. It must display:

- active architecture and model version;
- selection policy and primary metric;
- a selected marker beside the winning graph architecture;
- MLP, GraphSAGE, GCN-family, and GATv2 metric rows;
- improvement over the MLP baseline;
- candidate eligibility or failure reason;
- incumbent-retained or no-graph-value reason when no new activation occurs; and
- selection and serving timestamps.

The UI must not imply that all candidate architectures participate in live
routing. Exactly one selected graph architecture supplies the GNN verdict.

## 11. Winner refit and atomic activation

Training folds intentionally stop their message-passing graphs before target
intervals. Those older snapshots must not become the live user representations.
After selection and refit, the job builds a separate serving snapshot from all
currently eligible behavior.

The winning encoder, its user embeddings, decoder, preprocessing state, graph
snapshot identity, and manifest form one serving unit. Selecting a decoder `.pt`
file alone is invalid because embeddings are architecture-specific.

Activation proceeds as follows:

1. write and validate every candidate artifact in the immutable run directory;
2. select and refit the winning graph architecture;
3. build the current serving snapshot;
4. produce winner-specific user embeddings;
5. validate decoder/embedding dimensions, versions, schema, hashes, and score
   reproduction;
6. publish the winner bundle and embeddings without changing the current pointer;
7. atomically update the GNN serving version and selection summary; and
8. retain the preceding winner for rollback.

If any required artifact or embedding operation fails, the serving pointer is
not changed. `integrity-check` requires the decoder, embeddings, architecture,
feature schema, and graph snapshot identifiers to agree. A partial activation
therefore remains unavailable or falls back to the incumbent; it never mixes
candidate versions.

Compatibility-filename activation has been replaced with a version-resolved
serving pointer based on `dbo.IntegrityComponentStatus.ServingVersion`. Inference
uses the legacy decoder name only when no versioned pointer exists, allowing a
controlled transition for previously trained tenants.

## 12. Evaluation report required before first live activation

The evaluation report must be tenant-specific and must include:

1. exact data and label counts, including excluded and unlabeled rows;
2. label balance in every rolling training interval and the final holdout;
3. results for MLP, GraphSAGE, GCN-family, and GATv2 under equal budgets;
4. repeated-seed mean, spread, and worst-case performance;
5. PR-AUC, ROC-AUC, Brier score, calibration, and base-rate lift;
6. precision and recall at the actual HRBP review capacity;
7. false-positive rate and cold-start coverage;
8. performance stability by time interval and relevant user/category cohorts;
9. training time, peak memory, inference latency, and artifact size;
10. score reproducibility from the immutable artifacts;
11. explanation feasibility and expected GNNExplainer cost; and
12. a written activate, revise, or stop recommendation.

The selected graph architecture should be activated only if it materially
improves on the no-graph MLP and is operationally acceptable. If no graph
candidate beats the MLP reliably,
the correct conclusion is that the present graph or label regime does not yet
justify a live GNN—not that a GNN must be activated because it was built.

After the first accepted activation, the same versioned selection policy becomes
the standard operational process. Each subsequent run either atomically
activates its eligible winner or retains the incumbent with an explicit reason.

## 13. Activation checklist

Before enabling standard winner activation, verify all of the following:

- [ ] Both human label classes satisfy the approved train and holdout minimums.
- [ ] Outcome-maturity policy is defined and enforced.
- [ ] Candidate comparison has been repeated across approved seeds.
- [ ] At least one graph candidate materially exceeds the no-graph MLP under the approved metric.
- [ ] Calibration and HRBP-capacity thresholds have been selected.
- [ ] Tenant isolation and temporal leakage tests pass.
- [ ] The serving snapshot reproduces expected scores from its bundle.
- [ ] Immutable artifact retention exceeds rollback and explanation windows.
- [ ] v1 compatibility artifacts and embeddings remain available for rollback.
- [ ] `integrity-check` accepts the v2 feature schema and unavailable reasons.
- [ ] Detection Engines UI displays the candidate version and training status.
- [ ] Rollback has been exercised in the sandbox environment.
- [ ] Training and inference controls have been tested independently.
- [ ] The evaluation report has an explicit approval record.

## 14. Monitoring and retraining

The scheduled job remains weekly. Every attempt updates
`dbo.IntegrityComponentStatus` with its attempt status, stable reason, observed
gates, fold counts, holdout interval, candidate metrics, selected architecture,
selection reason, snapshot ID, and bundle prefix.

After production activation, monitoring should distinguish:

- training succeeded versus a model is currently available;
- candidate completion, eligibility, and selection outcomes;
- label-volume or class-balance skips;
- temporal-coverage skips;
- artifact upload or atomic activation failures;
- stale or missing embeddings;
- decoder/embedding version mismatch; and
- performance or calibration drift once new human outcomes mature.

A failed or skipped retraining attempt must preserve an older valid serving
version. It must never turn “no new model” into a zero-risk score.

## 15. Tests

Existing phase 2b automated tests cover:

- rolling boundary ordering and newest-date inclusion;
- graph/train/evaluation disjointness;
- disjoint rolling training intervals;
- target exclusion from message-passing history;
- tenant isolation;
- current-history serving snapshot construction;
- common forward contracts for MLP, GraphSAGE, GCN-family, and GATv2;
- one-epoch training of every graph candidate;
- fixed-epoch holdout isolation;
- feature-schema and artifact snapshot round trips; and
- restricted artifact deserialization.

The implementation includes tests for deterministic selection, MLP admission,
incumbent retention, partial candidate failure, version-resolved inference, and
independent tenant inference disablement. Remaining deployment-level tests cover:

- winner refit without rewriting candidate comparison artifacts;
- architecture-specific artifact and embedding activation;
- atomic serving-pointer change and rollback;
- Detection Engines winner and candidate rendering.

Multi-seed evaluation, maturity-delay enforcement, and activation rehearsal must
also be completed before v2 activation.

## 16. Implementation map

| Responsibility | File |
|---|---|
| Tenant graph, features, temporal folds, serving snapshot | `fraud-analytics-job/modeling/gnn/graph.py` |
| Candidate encoders, decoder, rolling optimization, metrics | `fraud-analytics-job/modeling/gnn/model.py` |
| Gates, labels, candidate bake-off, winner refit and activation | `fraud-analytics-job/modeling/train_gnn_model.py` |
| Canonical human-label contract | `fraud-analytics-job/modeling/labels.py` |
| Restricted graph snapshot bundle | `fraud-analytics-job/modeling/gnn/artifact_bundle.py` |
| Active training policy loader | `fraud-analytics-job/modeling/gnn/policy.py` |
| Live decoder inference | `integrity-check/inference/gnn_check.py` |
| Versioned policy schema | `schema-migration/alembic/versions/0059_gnn_scoring_policies.py` |
| Policy administration | `backend/routers/setup_router.py`, `frontend/src/components/GNNPolicyModal.tsx` |
| Phase 2b tests | `fraud-analytics-job/tests/test_gnn_graph.py`, `fraud-analytics-job/tests/test_gnn_rolling.py`, `fraud-analytics-job/tests/test_gnn_artifact_bundle.py` |
| Container infrastructure only | `terraform/environments/sandbox/main.tf` |
