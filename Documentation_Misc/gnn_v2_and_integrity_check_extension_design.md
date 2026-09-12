# GNN v2 and Asynchronous GNN Explanation Design

**Status:** Multi-candidate training and serving implemented; extension worker pending  
**Applies to:** `fraud-analytics-job`, `integrity-check`, new `integrity-check-extension`, `IntegrityDecisionResults`, administrative integrity UI  
**Last updated:** 2026-09-11

## 1. Purpose

This document defines the next GNN implementation for the Award Nomination System and the asynchronous explanation process that accompanies it.

The detailed operational training, evaluation, selection, and activation process is defined
separately in `Documentation_Misc/gnn_v2_training_strategy.md`.

The implementation-level service boundary, message settlement, artifact loading,
concurrency, and rollout contract for the asynchronous worker is defined in
`Documentation_Misc/integrity_check_extension_design.md`. That document is
authoritative for `integrity-check-extension` if the two documents differ.

The design preserves the central ELCE premise: the integrity decision is informed by independent engines, each of which produces its own finding before rules-based routing combines their results.

The four decision engines are:

1. Random Forest (RF)
2. Graph Analytics
3. Graph Neural Network (GNN)
4. Semantic description checks

RF, Graph Analytics, and GNN are fraud or integrity detection engines. The semantic engine assesses description quality and category alignment; it participates in routing but does not create fraud training labels.

This design covers:

- graph-native GraphSAGE, GCN-family, and GATv2 candidates with an MLP admission baseline;
- temporal training and evaluation without target leakage;
- model independence and a fair architecture-comparison process;
- versioned, reproducible model and graph artifacts;
- asynchronous case-level GNN explanations;
- a new `integrity-check-extension` container that performs explanation work outside the live inference path;
- persistence, Service Bus contracts, observability, security, testing, rollout, and rollback.

## 2. Explicitly deferred work

The **HRBP Random Audit Queue** is a separate, deferred feature. It is intended to acquire additional model-neutral, real human labels—especially legitimate outcomes—but it is not part of this implementation.

This design does not introduce synthetic labels, RF-derived labels, Graph Analytics-derived labels, or pseudo-labels to compensate for the current label shortage.

## 3. Decisions already made

### 3.1 One GNN engine, not multiple production votes

GraphSAGE, GCN-family, GATv2, and a future TGN implementation are candidate
architectures for one GNN engine. They must not appear as separate ELCE votes.
The MLP is a no-graph admission baseline, not a GNN candidate and never a live
GNN substitute.

The planned sequence is:

```text
Standard run: MLP baseline + GraphSAGE + GCN-family + GATv2
Selection:    choose one eligible graph winner
Inference:    emit one GNN verdict from that winner
Later:        allow TGN to join the candidate set after its lifecycle exists
```

Candidate comparison is a standard operational feature, analogous to the
Forecasting model bake-off. Only the selected graph architecture may produce the
live GNN score. An internal ensemble may be considered later only if it
demonstrates material improvement and still emits one GNN result.

### 3.2 The winner is selected operationally

GraphSAGE is no longer hard-coded as the production champion. Every eligible
run evaluates GraphSAGE, GCN-family, and GATv2 under the same folds and budget,
ranks them by a versioned selection policy, and selects one graph winner. The
winner must demonstrate sufficient value over the MLP baseline and pass
calibration, resource, artifact, and class-balance guardrails. Effective ties
retain the incumbent architecture to prevent needless weekly churn.

### 3.3 GNN explanation is asynchronous

Live scoring and routing must not wait for GNNExplainer. `integrity-check` persists the decision and routes the nomination first. It then requests an explanation when the configured trigger policy applies.

An explanation failure must never change:

- the GNN score;
- GNN availability;
- the composite decision;
- the nomination route or status; or
- a prior human decision.

### 3.4 The asynchronous host is named `integrity-check-extension`

The new component is intentionally broader than `gnn-explainer`, while remaining tied to asynchronous extensions of integrity-check processing.

Naming conventions:

| Concern | Name |
|---|---|
| Repository or project directory | `integrity-check-extension` |
| Azure Container Apps job | `award-integrity-check-extension` |
| Service name in persistent logs | `integrity-check-extension` |
| Logger namespace | `integrity_check_extension` |
| GNN explanation package | `extensions.gnn_explainer` |

The container is not a general background-job dumping ground. A future function belongs here only when it is an asynchronous, nomination-scoped extension of an already persisted integrity assessment and does not need to block the original score or route. Work with substantially different authority, scaling, dependencies, or lifecycle should use a separate component.

### 3.5 `IntegrityDecisionResults` remains the canonical result

No new explanation table is required. GNN explanation state and output are stored under the GNN result JSON in `dbo.IntegrityDecisionResults`. The extension worker may update only that nested explanation object and audit logs; it must not rewrite other engine results or routing fields.

### 3.6 Training and inference controls are separate

The active version in `dbo.GNNScoringPolicies` owns both controls.
`TrainingEnabled` controls future candidate training and preserves the last
valid serving winner when disabled. `InferenceEnabled` controls whether that
winner participates in live scoring. Disabled inference is persisted as
unavailable with reason `DISABLED_BY_POLICY`, never as a valid score of zero.
Publishing a policy requires no Terraform run: integrity-check reads it for the
next nomination, and the analytics job reads it when it next reaches the tenant.

Inference disablement is an exceptional safety action for conditions such as a
tenant-isolation defect, artifact corruption or incompatibility, severe scoring
drift, unacceptable latency/resource impact, a security incident, or a
tenant-specific policy suspension. Insufficient labels or one failed retraining
attempt merely preserves the incumbent model.

## 4. Current GNN baseline and the reason for v2

The current implementation is a real heterogeneous GNN, not merely an RF wrapper. It uses:

- a heterogeneous GraphSAGE encoder;
- user and nomination nodes;
- nomination and beneficiary relations plus reverse relations;
- stored user embeddings produced by scheduled training; and
- an MLP decoder over the nominator embedding, beneficiary embedding, and nomination feature vector.

The main current code is in:

- `fraud-analytics-job/modeling/gnn/graph.py`
- `fraud-analytics-job/modeling/gnn/model.py`
- `fraud-analytics-job/modeling/train_gnn_model.py`
- `integrity-check/inference/gnn_check.py`

However, much of the feature set mirrors RF feature engineering:

- the six current nomination features are also RF-style features;
- most user features are aggregates analogous to RF features; and
- handcrafted graph summaries such as concentration and reciprocal-pair counts may hide whether message passing adds value.

The current serving path also uses precomputed embeddings. A user absent from those embeddings produces `COLD_START_USER`. Therefore, although GraphSAGE can be inductive in principle, the current live path is not fully inductive.

Finally, the current one-shot temporal split assigns older labeled outcomes to graph history rather than supervised training. This prevents leakage, but wastes potentially useful labels. GNN v2 must retain leakage protection while recovering eligible historical labels through rolling temporal snapshots.

## 5. Model independence contract

GNN v2 may consume raw, time-valid nomination, user, organization, category, and relationship facts. It must not consume:

- RF scores, probabilities, risk levels, SHAP values, or RF flags;
- Graph Analytics scores, risk levels, detected patterns, or findings;
- semantic-engine scores, actions, or LLM conclusions;
- composite scores, decisive-engine lists, or final routes; or
- labels inferred from another model.

Sharing raw observables with another model does not violate independence. Sharing another model's output does.

Training labels must come from model-neutral human outcomes:

| `TrainingDisposition` | GNN target |
|---|---:|
| `FRAUD` | 1 |
| `LEGITIMATE` | 0 |
| `EXCLUDED` | ignored |
| `NULL` | ignored |

The same human outcome may train RF and GNN independently. Neither model may train from the other's prediction.

## 6. GNN v2 graph design

### 6.1 Node types

The baseline graph contains:

- `user`
- `nomination`
- `category`

Tenant boundaries are absolute. A graph, batch, snapshot, embedding set, and explanation request must contain exactly one tenant.

### 6.2 Relation types

The proposed baseline relations are:

```python
("user", "nominates", "nomination")
("nomination", "benefits", "user")
("nomination", "belongs_to", "category")
```

Each relation must have the corresponding reverse relation required by the encoder.

The following relationship is optional and may be enabled only if it is reliably available at submission time and improves temporal evaluation:

```python
("user", "reports_to", "user")
```

Approver relationships are excluded from the initial baseline. They can introduce workflow facts that are absent or unresolved at nomination submission time.

### 6.3 User attributes

The baseline should use a deliberately small set of structural attributes that neighborhood aggregation cannot reliably reconstruct on its own:

```python
USER_FEATURE_COLUMNS_V2 = [
    "LogNominationsMade",
    "LogNominationsReceived",
    "LogUniqueCounterparties",  # subject to ablation
]
```

The initial graph-native baseline excludes:

- `AvgAmountGiven`
- `StdAmountGiven`
- `AvgAmountReceived`
- `UniqueBeneficiaries`
- `UniqueNominators`
- `ConcentrationRatio`
- `ReciprocalPairCount`

These are not permanently prohibited. They may be reintroduced individually only through an ablation that demonstrates out-of-time improvement. In particular, concentration and reciprocal behavior should first be represented by graph structure rather than duplicated as handcrafted summaries.

### 6.4 Nomination attributes

The proposed nomination attributes are:

```python
NOMINATION_FEATURE_COLUMNS_V2 = [
    "LogAmount",
    "CategoryRelativeAmountRobustZScore",
    "DaysBeforeGraphCutoff",
    "DayOfWeekSin",
    "DayOfWeekCos",
    "MonthSin",
    "MonthCos",
    "HistoricalStatus",
]
```

Rules for these fields:

- `LogAmount` reduces sensitivity to the long tail of award values.
- `CategoryRelativeAmountRobustZScore` is calculated only from facts available before the target time and uses a robust estimator within the tenant/category population.
- `DaysBeforeGraphCutoff` records recency relative to the snapshot cutoff. It must never become negative for a node in the message-passing graph.
- cyclical encodings replace ordinal day and month integers.
- `HistoricalStatus` is permitted only for historical nomination nodes whose status was known by the snapshot cutoff. The target nomination must not expose a post-submission status.

The baseline removes redundant raw or thresholded variants:

- raw `Amount` beside `LogAmount`;
- tenant-wide `AmountZScore` beside the category-relative robust value;
- `IsHighAmount`;
- ordinal `DayOfWeek` and `Month`; and
- `IsWeekend`, which is derivable from day-of-week encoding.

### 6.5 Description content

Description embeddings are excluded from the core GNN v2 baseline. The semantic engine already specializes in description meaning, and the GNN should preserve graph-centric diversity.

This does not prevent a later, separately evaluated multimodal experiment. Such an experiment must be treated as a challenger, with its loss of engine diversity explicitly measured and documented.

### 6.6 Candidate nomination contract

The nomination being scored must not be inserted into the historical message-passing graph in a way that lets its target outcome or future neighbors influence its embedding. Its live feature vector is passed to the decoder together with the time-valid nominator and beneficiary representations.

If online inductive inference is introduced, the neighborhood supplied to the encoder must still be cut off immediately before the candidate nomination. The exact cutoff and snapshot identifiers must be persisted with the score.

## 7. Temporal training design

### 7.1 Invariant

For every supervised target nomination at time `T`, every node attribute, edge, aggregate, label-derived attribute, and neighbor used to score it must have been knowable before `T`.

### 7.2 Rolling temporal snapshots

Replace the single 60/20/20 partition with rolling origin evaluation and training windows. Conceptually:

1. build graph history through cutoff `T0`;
2. train on labeled nominations in the following eligible interval;
3. advance the cutoff;
4. rebuild or increment the graph using only facts now available;
5. train or evaluate the next interval; and
6. keep the final interval untouched for out-of-time evaluation.

This permits an early confirmed outcome to become ordinary historical context for a later target without leaking the later outcome backward.

The initial implementation divides the configured lookback into `fold_count + 2`
chronological segments (`fold_count=3` by default). Each fold uses expanding
history, one disjoint training segment, and the immediately following evaluation
segment. Training uses fixed epochs across all disjoint training segments; it
does not early-stop on the final interval. The newest segment is evaluated once
as the untouched holdout. The fold count, every cutoff, and observed counts are
recorded in the artifact manifest. Outcome-maturity delay remains to be locked
before first live activation.

### 7.3 Outcome maturity

A human outcome may become a training label only after it is final and was recorded before the snapshot cutoff. Later corrections require a new model version; old artifacts remain immutable.

### 7.4 Training gates

A training attempt must report, at minimum:

- train positive count;
- train negative count;
- evaluation positive count;
- evaluation negative count;
- unlabeled historical nomination count;
- excluded count;
- tenant user count;
- cold-start user count; and
- the configured minimums.

Training must not proceed when either class is absent from training or evaluation. Exact minimum counts remain a configuration decision to be locked before implementation. The UI must show the real temporal counts and reason for every skipped attempt.

## 8. Standard architecture selection

### 8.1 Required baselines

Every standard GNN v2 training run must compare, using the same tenant
population, label set, temporal folds, feature availability, and training budget:

1. a no-graph MLP baseline;
2. heterogeneous GraphSAGE v2;
3. a heterogeneous GCN-family candidate implemented with PyG `GraphConv`; and
4. a heterogeneous GATv2 candidate.

The MLP is always evaluated but is not eligible to serve as GNN. TGN may join a
future candidate set because it introduces event memory and a materially
different training and serving lifecycle. It would remain a candidate for the
single GNN engine, not an additional production vote.

### 8.2 Required metrics

Accuracy alone is not an acceptable selection metric. Evaluation must include:

- out-of-time PR-AUC;
- precision at the available HRBP review budget;
- recall at fixed review capacity or fixed false-positive rate;
- false-positive rate;
- calibration and Brier score;
- per-tenant and per-time-window stability;
- cold-start coverage;
- training duration and memory;
- inference latency and memory; and
- explanation feasibility and cost.

### 8.3 Selection policy

The initial primary metric is out-of-time holdout PR-AUC. A graph architecture
is eligible only when it beats the MLP baseline by the configured minimum and
passes label, calibration, latency, memory, and artifact-validation guardrails.
The policy is versioned and includes an incumbent tie tolerance. It emits a
stable selection reason such as `HIGHEST_ELIGIBLE_PR_AUC`,
`INCUMBENT_RETAINED_WITHIN_TOLERANCE`, `NO_GRAPH_VALUE_OVER_MLP`, or
`INSUFFICIENT_ELIGIBLE_CANDIDATES`.

If no graph candidate qualifies, the MLP does not become the GNN. The previous
valid GNN remains active, or the component remains unavailable when there is no
incumbent.

### 8.4 Ablation sequence

At minimum, compare:

1. MLP with the same v2 endpoint and nomination attributes but no graph message passing;
2. graph topology with the minimal v2 node attributes;
3. complete GraphSAGE v2;
4. GraphSAGE v2 plus each optional handcrafted feature group; and
5. GraphSAGE v2 with each optional relation type.

The operational winner must provide meaningful improvement beyond the no-graph
baseline. If no graph candidate does, the run records `NO_GRAPH_VALUE_OVER_MLP`.
That result must not be hidden by deploying the MLP under a GNN label.

## 9. Artifact and snapshot contract

### 9.1 Versioned bundle

Training publishes an immutable, tenant-specific bundle:

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

Large ID maps may be stored as separate immutable files referenced by the manifest.
Candidate files preserve the selection comparison. The `serving` files contain
the selected architecture after full matured-label refit and are the only model
weights loaded by `integrity-check`.

### 9.2 Manifest contents

`manifest.json` must include:

- tenant ID;
- model version;
- graph snapshot ID and cutoff;
- training and evaluation time ranges;
- feature-schema version;
- exact feature names and order;
- node and relation types;
- transformations, fitted scalers, and category handling;
- artifact hashes;
- encoder and decoder architecture parameters;
- training gates and observed counts;
- evaluation metrics;
- candidate eligibility and guardrail results;
- selection-policy version and primary metric;
- selected architecture, metric value, MLP baseline, improvement, incumbent,
  and stable selection reason;
- winner-refit metadata;
- source-code or image revision;
- Python, PyTorch, and PyTorch Geometric versions; and
- artifact creation time.

### 9.3 Reproducibility invariant

The GNN score and its later explanation must reference the same:

- tenant;
- model version;
- feature schema;
- encoder and decoder artifacts;
- graph snapshot;
- embedding or mapping version; and
- preprocessing parameters.

The explanation worker must never substitute the latest model or latest graph for the version used at inference.

### 9.4 Retention

Artifact and snapshot retention must exceed the maximum explanation delay, operational retry window, investigation period, and rollback period. Deleting an artifact that is still referenced by an integrity result is prohibited. The concrete retention interval must be locked with the operational policy.

### 9.5 Selection persistence

Selection is persisted at three levels:

1. `manifest.json` is the immutable authority for the complete candidate bake-off
   and why the winner was selected.
2. The current `GNN` row in `dbo.IntegrityComponentStatus` uses
   `ServingVersion`, `ServingAsOf`, and `RunId` for the active model and stores
   the selection summary and candidate metrics in `DiagnosticsJson.selection`.
   Its system-versioned history preserves prior winners.
3. `dbo.IntegrityDecisionResults.GnnResultJson` stores the architecture, model
   version, and snapshot that scored that specific nomination.

`dbo.Tenants.integrity_config` stores selection and routing policy, not the
dynamically selected winner. No new selection table is required.

## 10. Live GNN inference

`integrity-check` remains responsible for synchronous GNN inference. The output is persisted inside `GnnResultJson` in `dbo.IntegrityDecisionResults` and participates in rules-based routing alongside the other engines.

The GNN result should include at least:

```json
{
  "available": true,
  "score": 73,
  "probability": 0.731245,
  "risk_level": "HIGH",
  "architecture": "graphsage",
  "model_version": "gnn-v2-20260909-t1",
  "training_policy_version": 3,
  "scoring_policy_version": 4,
  "graph_snapshot_id": "gnn-graph-v2-20260909-t1",
  "graph_snapshot_as_of": "2026-09-09T00:00:00Z",
  "feature_schema_version": "gnn-v2",
  "explanation": {
    "method": "GNNEXPLAINER",
    "status": "REQUESTED",
    "request_id": "gnnexp:t1:n13881:gnn-v2-20260909-t1",
    "requested_at": "2026-09-09T18:00:00Z"
  }
}
```

The decoder, architecture-specific user embeddings, preprocessing state, model
version, and graph snapshot are one serving unit. `integrity-check` resolves the
unit from `dbo.IntegrityComponentStatus.ServingVersion`; it must not activate a
decoder filename independently of its matching embeddings.

When unavailable, the result must retain the existing availability contract, including a stable reason code and human-readable detail. A missing model, missing snapshot, schema mismatch, or cold-start condition must not be represented as a legitimate score of zero.

## 11. GNN explanation policy

### 11.1 Purpose

GNNExplainer supplies case-level evidence analogous in governance purpose to RF SHAP. It explains what subgraph relationships and input features most influenced this GNN prediction.

It is not:

- a second scoring engine;
- a causal proof of fraud;
- a replacement for Graph Analytics findings;
- an LLM-generated narrative; or
- a reason to delay routing.

### 11.2 Trigger

The initial policy requests an explanation when:

- the GNN model is available;
- the score was successfully persisted;
- the GNN risk level is `MEDIUM`, `HIGH`, or `CRITICAL`; and
- an explanation for the same nomination and model version is neither completed nor already active.

The trigger threshold should be tenant-configurable in `dbo.Tenants.integrity_config`, with `MEDIUM` as the initial default. `NONE` and `LOW` may be explained manually or by a future sampling policy, but are not requested automatically in the first release.

### 11.3 Lifecycle states

The stable explanation statuses are:

| Status | Meaning |
|---|---|
| `NOT_REQUESTED` | Trigger policy did not request an explanation |
| `REQUESTED` | Request recorded and awaiting or queued for delivery |
| `RUNNING` | Extension worker claimed the request |
| `COMPLETED` | Valid explanation persisted |
| `FAILED` | Processing failed after allowed retries or publishing failed |
| `SNAPSHOT_UNAVAILABLE` | Exact graph snapshot cannot be loaded |
| `MODEL_VERSION_MISMATCH` | Request and persisted result/artifacts do not agree |

Failure records must include a stable `reason` code, a concise `detail`, attempt count, and last-attempt time. Sensitive stack traces remain in platform telemetry rather than result JSON.

### 11.4 Completed explanation contract

```json
{
  "method": "GNNEXPLAINER",
  "status": "COMPLETED",
  "request_id": "gnnexp:t1:n13881:gnn-v2-20260909-t1",
  "model_version": "gnn-v2-20260909-t1",
  "graph_snapshot_id": "gnn-graph-v2-20260909-t1",
  "graph_snapshot_as_of": "2026-09-09T00:00:00Z",
  "generated_at": "2026-09-09T18:01:42Z",
  "fidelity": 0.82,
  "stability": 0.76,
  "top_relationships": [
    {
      "relationship": "Nominator repeatedly nominated the same beneficiary",
      "counterparty_user_id": 507,
      "supporting_nomination_count": 4,
      "importance": 0.31
    }
  ],
  "top_features": [
    {
      "feature": "Category-relative award amount",
      "value": 2.14,
      "importance": 0.22,
      "direction": "increases_risk"
    }
  ],
  "summary": "Repeated nominator-beneficiary activity and a high category-relative amount contributed most to this GNN score."
}
```

The exact evidence vocabulary must avoid raw tensor indexes. Internal node and edge indexes are resolved to tenant-valid business facts before persistence.

If an LLM narrative is added later, it must be stored separately as `llm_narrative`, explicitly labeled **LLM explanation**, and grounded only in the structured GNN evidence. The deterministic structured explanation remains authoritative.

### 11.5 Fidelity and stability

The worker runs the explainer with multiple controlled seeds. It records:

- **fidelity:** how materially masking selected evidence changes the predicted GNN probability; and
- **stability:** how consistently the same important relationships/features appear across runs.

Low-fidelity or unstable output must be labeled accordingly in the UI. It must not be rendered as a confident explanation merely because an algorithm returned a ranked list.

## 12. Service Bus contract

### 12.1 Event

Event type:

```text
gnn.explanation.requested
```

Message body:

```json
{
  "event_type": "gnn.explanation.requested",
  "schema_version": 1,
  "request_id": "gnnexp:t1:n13881:gnn-v2-20260909-t1",
  "nomination_id": 13881,
  "tenant_id": 1,
  "gnn_model_version": "gnn-v2-20260909-t1",
  "gnn_scoring_policy_version": 4,
  "embedding_as_of": "2026-09-09T00:00:00Z",
  "graph_snapshot_id": "gnn-graph-v2-20260909-t1",
  "request_reason": "MEDIUM_OR_HIGHER",
  "source_message_id": "nomination-submitted-message-id",
  "requested_at": "2026-09-09T18:00:00Z"
}
```

The message is a pointer and reproducibility contract. It must not contain the graph, embeddings, nomination description, names, email addresses, or other avoidable PII.

### 12.2 Identity and idempotency

Both `request_id` and Service Bus `MessageId` use a deterministic value:

```text
gnnexp:t<tenant_id>:n<nomination_id>:<gnn_model_version>
```

At-least-once delivery is expected. Reprocessing the same request must not duplicate the explanation, alter the route, or append contradictory audit events.

The integrity-check publisher now accepts an explicit message ID and uses the
deterministic request ID for this workflow. A random UUID is not sufficient.

### 12.3 Delivery and retry behavior

- `integrity-check` writes `REQUESTED` before publishing.
- A successful publish records `published_at` when practical.
- A publish failure does not fail nomination routing; it records `FAILED` with `PUBLISH_FAILED` or leaves a recoverable requested state with an explicit last publish error.
- A reconciliation job republishes failed or stale requests using the same deterministic message ID.
- Transient worker errors abandon the message for retry.
- Invalid payloads and irrecoverable contract violations are dead-lettered.
- Permanent missing-artifact or version-mismatch outcomes are persisted before message completion or dead-lettering according to the locked operational policy.

## 13. `integrity-check-extension` design

### 13.1 Proposed layout

```text
integrity-check-extension/
  main.py
  dispatcher.py
  config.py
  utils/
    db.py
    service_bus.py
    logging.py
  extensions/
    gnn_explainer/
      __init__.py
      handler.py
      explainer.py
      graph_snapshot.py
      formatter.py
      contracts.py
  tests/
```

### 13.2 Dispatch flow

`main.py` receives the Service Bus message. `dispatcher.py` validates `event_type` and schema version, then invokes the registered extension handler. Unknown types are rejected explicitly; they must not silently succeed.

### 13.3 GNN explanation worker flow

For each request:

1. validate the payload and required identifiers;
2. verify that the nomination belongs to the stated tenant;
3. claim the deterministic request through the existing processed-event/idempotency mechanism or its extension equivalent;
4. load `dbo.IntegrityDecisionResults` for the nomination;
5. verify the persisted GNN result, model version, feature schema, and snapshot ID;
6. return idempotent success if the same explanation is already `COMPLETED`;
7. conditionally change the explanation status to `RUNNING`;
8. load the exact encoder, decoder, preprocessing data, graph snapshot, and mappings;
9. reconstruct the scored case and verify the reproduced probability is within a defined tolerance of the persisted probability;
10. run GNNExplainer with controlled seeds;
11. calculate fidelity and stability;
12. resolve internal indexes into safe, tenant-scoped business evidence;
13. atomically update only `GnnResultJson.explanation`;
14. write nomination-scoped completion or failure logs; and
15. complete, abandon, or dead-letter the Service Bus message according to failure type.

If score reproduction exceeds the permitted tolerance, the worker records a stable failure such as `SCORE_REPRODUCTION_MISMATCH`; it must not persist a misleading explanation.

## 14. Database update and concurrency rules

### 14.1 Atomic nested update

The extension worker must use an atomic JSON update, such as SQL Server `JSON_MODIFY` with `JSON_QUERY`, to replace only the GNN `explanation` object.

The update must be conditional on:

- matching nomination and tenant;
- matching GNN model version;
- matching graph snapshot ID; and
- the explanation not already being completed for that request.

It must preserve:

- RF result JSON;
- Graph Analytics result JSON;
- Semantic result JSON;
- all other GNN result properties;
- composite score and risk;
- decisive engines;
- review scope;
- final route;
- training disposition; and
- human review state.

### 14.2 Inference retry protection

The current integrity-result persistence path can write the full GNN JSON. Before explanation requests are enabled, it must be changed or verified so a duplicate `nomination.submitted` event cannot erase an explanation already written by the extension worker.

Acceptable approaches are:

- preserve and merge an existing explanation object when rewriting the same model result; or
- make the original decision row immutable after successful routing and update only explicitly mutable nested fields.

The second approach is preferable if it is compatible with existing idempotency and recovery behavior.

### 14.3 Audit fields

An explanation-only update updates the row's `UpdatedAt` while leaving `CreatedAt` and `ScoredBy` unchanged. The present table has no `UpdatedBy` column, so `svc:integrity-check-extension` attribution is recorded in the nomination-scoped persistent log rather than by changing the original scorer.

## 15. Logging and observability

### 15.1 Nomination-scoped logs

The extension writes to `dbo.Nomination_Logs` with:

- `service = integrity-check-extension`
- `logger = integrity_check_extension.gnn_explainer`
- the nomination ID and tenant ID;
- the deterministic request ID; and
- the source Service Bus message ID where available.

Expected messages include:

- `GNN explanation requested`
- `GNN explanation started`
- `GNN explanation completed`
- `GNN explanation failed`
- `GNN explanation request already completed — skipping`

Completion details include model version, graph snapshot ID, duration, fidelity, stability, and evidence counts. Failure details include a stable reason code and concise non-sensitive detail.

The Nomination Logs **Integrity check only** filter must explicitly include both namespaces:

```sql
logger LIKE 'integrity[_]check.%'
OR logger LIKE 'integrity[_]check[_]extension.%'
```

The bracket expressions make the underscores literal in SQL Server. The implementation must not rely on accidental SQL `LIKE` wildcard behavior for underscore characters.

### 15.2 Platform metrics

Operational monitoring must include:

- request count by tenant and status;
- queue age and depth;
- explanation duration percentiles;
- completion, retry, and permanent-failure rates;
- snapshot-unavailable and model-version-mismatch counts;
- score-reproduction mismatch count;
- fidelity and stability distributions;
- dead-letter count; and
- stale `REQUESTED` or `RUNNING` records.

Alerts should target operational failures, not individual risk findings.

## 16. Security and privacy

The extension uses a managed identity with least privilege. It requires only:

- receive permission on the dedicated explanation subscription;
- read access to the referenced model/snapshot artifacts;
- tenant-scoped read access needed to reconstruct the scored case;
- permission to update only the GNN explanation JSON and audit fields; and
- permission to append nomination logs.

It must not have permission to change nomination status, final routing, human dispositions, or other engine outputs.

The tenant ID in the message is untrusted input. The worker verifies nomination ownership against the database before reading history or artifacts. Cross-tenant nodes, edges, mappings, and explanation evidence are rejected and logged as security-relevant contract violations.

Persisted evidence should use stable internal IDs and concise business descriptions. Names, email addresses, and full nomination descriptions should not be copied into the Service Bus message or explanation JSON unless a later reviewed requirement makes them necessary.

## 17. User experience

### 17.1 GNN card

The GNN swim lane/card in Model Analysis and HRBP Review should show:

- availability;
- score, probability, and risk level;
- selected architecture, model version, and snapshot date when expanded;
- explanation state; and
- completed top relationships and top features.

Suggested state text:

- `GNN explanation not requested`
- `GNN explanation pending`
- `GNN explanation in progress`
- `GNN explanation unavailable: <concise reason>`
- `GNN explanation completed`

The UI refreshes or polls for the asynchronous result. It must not imply that the nomination is still waiting for routing.

### 17.2 Evidence presentation

The heading should explicitly identify the source, for example **GNN Model (GNNExplainer) Breakdown**.

Relationships and features should be visually separated. Each item may show importance, direction when meaningful, and supporting counts. Fidelity and stability should be available in an expandable technical section for data scientists and administrators.

Any later LLM-written prose is displayed under an **LLM explanation** label and must not replace the structured evidence.

### 17.3 Logs

The nomination drawer shows request, start, completion, and failure events alongside synchronous integrity-check logs when **Integrity check only** is selected.

### 17.4 Detection Engines selection view

The read-only GNN component view shows the current serving architecture and
version separately from the latest selection attempt. It presents all candidate
metrics, the selected marker, MLP improvement, eligibility/failure reasons,
selection policy and reason, incumbent-retention status, and selection/serving
timestamps. It must state that only the selected graph architecture produces the
single live GNN verdict.

## 18. Infrastructure and deployment

Implementation requires:

- a new `integrity-check-extension` application directory and container image;
- an Azure Container Apps event-driven job named `award-integrity-check-extension`;
- a dedicated Service Bus subscription filtered to `gnn.explanation.requested`;
- an updated `email-processor` filter that excludes
  `gnn.explanation.requested` rather than receiving the internal work item;
- managed identity and RBAC assignments;
- artifact storage configuration;
- database and persistent-log configuration;
- retry, lock-renewal, concurrency, scale, and dead-letter settings;
- Terraform changes;
- CI/CD build and deployment changes; and
- environment-specific feature flags.

The heavy PyTorch Geometric/explainer dependencies remain outside the latency-sensitive `integrity-check` image.

## 19. Testing strategy

### 19.1 GNN data and graph tests

- tenant isolation for nodes, edges, labels, and mappings;
- no post-cutoff nomination or outcome leakage;
- target nomination exclusion from message-passing history;
- forward/reverse relation consistency;
- category-node and optional reporting-line construction;
- cyclical time encoding;
- robust category-relative amount calculation;
- missing user/category handling;
- rolling-window coverage and disjoint evaluation intervals; and
- feature-name/order parity between training and inference.

### 19.2 Model tests

- MLP versus graph ablations;
- standard MLP admission and graph-architecture winner selection;
- deterministic selection reasons and incumbent tie handling;
- winner refit and architecture-specific artifact/embedding activation;
- deterministic seeded training within documented tolerance;
- probability calibration;
- cold-start behavior;
- model and snapshot manifest validation;
- artifact hash validation; and
- temporal evaluation report generation.

### 19.3 Publisher and contract tests

- trigger policy for each GNN risk level;
- no request when GNN is unavailable;
- deterministic request and message IDs;
- valid schema versioning;
- no PII in the message body;
- publish failure does not change routing; and
- reconciliation republishes with the same identity.

### 19.4 Worker tests

- valid request end to end;
- duplicate delivery and completed-request no-op;
- wrong tenant rejection;
- model-version mismatch;
- snapshot unavailable;
- artifact hash mismatch;
- score-reproduction mismatch;
- transient retry and permanent failure;
- dead-letter behavior;
- fidelity/stability calculation;
- index-to-business-evidence formatting; and
- atomic JSON update preserving every unrelated field.

### 19.5 Integration and UI tests

- Service Bus round trip in a non-production environment;
- explanation status transitions;
- Nomination Logs visibility under the integrity-only filter;
- HRBP Review and Model Analysis rendering;
- late completion after the human review was already resolved;
- stale request reconciliation; and
- disabled-feature behavior.

## 20. Rollout plan

1. Lock this design and remaining configuration decisions.
2. Introduce the v2 feature schema and immutable artifact manifest without replacing v1 artifacts.
3. Retain the implemented rolling temporal dataset and common-candidate foundation.
4. Replace optional benchmarking and the fixed GraphSAGE wrapper with the standard
   MLP + GraphSAGE + GCN-family + GATv2 bake-off and versioned winner selection.
5. Implement winner refit, architecture-specific artifacts and embeddings, and
   atomic activation through `IntegrityComponentStatus.ServingVersion`.
6. Produce the first evaluation report and either enable standard winner
   activation or keep GNN unavailable.
7. Add exact architecture/model/snapshot identifiers to live GNN results.
8. Scaffold `integrity-check-extension`, Service Bus contract, and infrastructure with explanation requests disabled.
9. Implement offline GNNExplainer fidelity, stability, and score-reproduction checks.
10. Implement atomic `GnnResultJson.explanation` updates and reconciliation.
11. Add nomination-scoped logs and UI states, including the selected architecture
    and candidate comparison in Detection Engines.
12. Deploy dark, then enable inference for a demo tenant.
13. Compare operational cost, latency, fidelity, stability, and reviewer usefulness.
14. Enable additional tenants only after acceptance criteria are met.

The temporary `GNN_BENCHMARK_ENABLED` and `GNN_V2_PROMOTION_ENABLED` controls are
removed by this revision. Candidate comparison becomes standard. New training
and tenant-level inference controls remain available independently for
operational safety.

## 21. Rollback plan

- Keep the current GNN v1 artifacts and inference path available during v2 validation.
- Version v2 artifacts rather than overwriting v1 filenames.
- Disable explanation publishing through configuration without disabling GNN scoring.
- Stop or scale the extension job to zero without affecting nomination routing.
- Preserve already completed explanation JSON as historical audit data.
- Roll back the GNN winner by changing `IntegrityComponentStatus.ServingVersion`
  to a retained immutable version, never by replacing an artifact in place.

## 22. Acceptance criteria

The feature is complete only when all of the following are true:

1. GNN v2 uses tenant-isolated, time-valid graph data and the approved v2 feature schema.
2. GNN v2 consumes no RF, Graph Analytics, semantic, composite, or routing outputs.
3. Training uses only explicit `FRAUD` and `LEGITIMATE` human dispositions; `EXCLUDED` and unlabeled rows are ignored.
4. Temporal tests demonstrate that no target or future outcome leaks into graph history or features.
5. Every standard run compares the MLP, GraphSAGE, GCN-family, and GATv2 under
   the same data, folds, labels, and training budget.
6. The MLP is an admission baseline and can never be served or labeled as GNN.
7. The selected graph winner passes the versioned selection policy and emits one
   GNN probability, score, risk level, architecture, model version, and graph
   snapshot reference.
8. The winner's encoder, embeddings, decoder, preprocessing state, and snapshot
   are activated atomically from an immutable version.
9. The manifest, component status, nomination result, and Detection Engines UI
   expose the selected architecture at their appropriate audit level.
10. Training and inference can be disabled independently; either condition is
    represented explicitly and never as a valid zero-risk score.
11. Medium-or-higher GNN results create a deterministic asynchronous explanation request under the initial policy.
12. Scoring, persistence, and nomination routing complete without waiting for an explanation.
13. The worker reproduces the persisted score within tolerance before explaining it.
14. The explanation uses the exact model and graph snapshot referenced by the original result.
15. Duplicate delivery is idempotent.
16. The worker updates only the nested GNN explanation and required audit fields.
17. Explanation failure cannot alter engine verdicts, final route, nomination status, or human review state.
18. Nomination-scoped extension logs appear in the integrity-only log view.
19. HRBP Review and Model Analysis clearly identify GNNExplainer output, its pending/failed state, and any low-confidence explanation.
20. Tenant isolation and least-privilege security tests pass.
21. Feature controls, monitoring, dead-letter handling, reconciliation, and rollback have been exercised in a non-production environment.

## 23. Remaining implementation decisions

The following choices remain open and must be resolved before their associated implementation phase:

1. ~~Initial rolling-window partition and stride.~~ Resolved for the baseline: `fold_count + 2` equal chronological segments, three folds by default, expanding graph history, disjoint train segments, and the newest segment as holdout. Outcome-maturity delay remains open.
2. Minimum positive and negative labels required per train and evaluation interval.
3. ~~Whether `LogUniqueCounterparties` belongs in the baseline or only an ablation.~~ Resolved: it is included in the initial v2 baseline and remains subject to measured ablation.
4. ~~Whether `reports_to` is sufficiently complete and time-valid for the initial graph.~~ Resolved: it is excluded from the initial v2 graph.
5. ~~Exact `HistoricalStatus` encoding and treatment of status corrections.~~ Resolved for the baseline: historical graph nodes use `Pending=0`, `Approved=1`, `Paid=2`; target nominations always receive `0`, and later corrections require a new immutable model version.
6. ~~Minimum PR-AUC improvement over MLP and incumbent tie tolerance.~~ Resolved
   for selection policy v1 as `0.02` and `0.01`, respectively. Probability
   calibration and resource guardrail thresholds remain open.
7. Snapshot serialization format, mapping storage, and retention interval.
8. Permitted score-reproduction tolerance for explanations.
9. Number of explainer seeds and minimum fidelity/stability display thresholds.
10. Retry counts, lock duration, stale-request interval, and dead-letter policy.
11. Whether a later LLM narrative is worthwhile after structured explanations are validated.

None of these open choices changes the foundational decisions: one independent
GNN engine, standard multi-candidate selection with an MLP admission baseline,
one atomically activated graph winner, temporal and tenant isolation, immutable
reproducible artifacts, and asynchronous non-blocking explanation through
`integrity-check-extension`.

## 24. Future work

Future work, each requiring its own approved design or extension to this one, includes:

- the HRBP Random Audit Queue for legitimate-label acquisition;
- adding TGN to the standard candidate set after its event-memory training and
  serving lifecycle is designed;
- active-learning or sampling strategies that do not contaminate evaluation;
- optional explanation sampling for low-risk cases;
- multimodal GNN challengers using description representations;
- model-level explanation and diagnostics using XGNN-style techniques; and
- an explicitly labeled, grounded LLM narrative for completed structured GNN explanations.
