# Integrity Check Extension Design

**Status:** Design draft; request publisher implemented, extension worker pending  
**Applies to:** `integrity-check`, `integrity-check-extension`, `fraud-analytics-job`, Azure Service Bus, `dbo.IntegrityDecisionResults`, HRBP Review, Model Analysis  
**Last updated:** 2026-09-11

## 1. Purpose

`integrity-check-extension` is the asynchronous companion to the latency-sensitive
`integrity-check` service. Its first extension is nomination-scoped GNN explanation
using GNNExplainer.

The extension explains an already persisted GNN verdict. It does not score the
nomination, participate in model fusion, route the nomination, or create a
training label. Live integrity processing always completes before explanation
work begins.

The GNN training and architecture-selection contract remains authoritative in
`Documentation_Misc/gnn_v2_training_strategy.md`. The broader GNN design remains
in `Documentation_Misc/gnn_v2_and_integrity_check_extension_design.md`. This
document is the implementation contract for the extension service itself.

## 2. Scope

This implementation includes:

- receiving `gnn.explanation.requested` events;
- validating tenant, nomination, model, snapshot, and message identity;
- loading the exact immutable GNN serving bundle;
- reproducing the persisted GNN probability before explanation;
- running seeded GNNExplainer passes;
- calculating fidelity and stability;
- formatting safe node, relationship, and feature evidence;
- atomically updating only `GnnResultJson.explanation`;
- nomination-scoped logging, retry, dead-letter, reconciliation, and monitoring;
- read-only rendering in HRBP Review and Model Analysis; and
- dark deployment followed by tenant-specific activation.

The following are out of scope:

- changing a GNN score or availability;
- changing composite risk, routing, nomination status, or human decisions;
- writing `TrainingDisposition`;
- generating an LLM narrative;
- explaining RF, Graph Analytics, or semantic results;
- synchronous explanation inside `integrity-check`; and
- using candidate `metrics.json` files at runtime.

## 3. Existing foundation

The request side already exists in `integrity-check`:

- `inference/gnn_explanation.py` applies the tenant trigger policy and creates a
  deterministic request;
- `inference/handler.py` persists and routes the nomination before publishing;
- `utils/service_bus_publisher.py` supports an explicit Service Bus message ID;
- `utils/db.py` preserves a matching `RUNNING` or `COMPLETED` explanation during
  an inference retry; and
- publish failures update only the matching nested explanation object.

The default tenant policy keeps `ExplanationEnabled` false. No extension request
should be published until the Service Bus subscription, worker, persistence
contract, and non-production validation described here are deployed.

## 4. Service boundary

```text
integrity-check
  score → persist → route
                  |
                  +-- publish gnn.explanation.requested
                                      |
                                      v
Azure Service Bus: award-events / gnn-explanation-processor
                                      |
                                      v
integrity-check-extension
  validate → claim → reproduce score → explain → persist evidence → log
                                      |
                                      v
dbo.IntegrityDecisionResults.GnnResultJson.explanation
```

`integrity-check-extension` may host another feature only when that feature is:

- an asynchronous continuation of a persisted integrity assessment;
- nomination-scoped;
- non-blocking for original scoring and routing; and
- compatible with the same security and operational lifecycle.

A workload with materially different authority, dependencies, scaling, or
lifecycle requires a separate service.

## 5. Project structure

```text
integrity-check-extension/
  main.py
  dispatcher.py
  config.py
  requirements.txt
  Dockerfile
  utils/
    azure_credential.py
    db.py
    logging.py
    model_artifacts.py
    service_bus.py
  extensions/
    gnn_explainer/
      __init__.py
      contracts.py
      handler.py
      artifact_bundle.py
      model.py
      explainer.py
      fidelity.py
      formatter.py
  tests/
    fixtures/
```

`main.py` owns message settlement and lock renewal. `dispatcher.py` validates
the event envelope and delegates by event type. Extension handlers return a
settlement outcome; they do not directly complete or dead-letter messages.

## 6. Service Bus topology

The event remains on the existing `award-events` topic. Add a subscription:

```text
gnn-explanation-processor
```

with the exact SQL filter:

```sql
event_type = 'gnn.explanation.requested'
```

The current `email-processor` subscription accepts every event except
`nomination.submitted`. Before explanations are enabled, its filter must also
exclude `gnn.explanation.requested`; otherwise the auxiliary email processor
will receive internal explanation work.

The extension managed identity receives `Azure Service Bus Data Receiver` on
the topic. It receives no sender permission unless reconciliation is later
implemented inside the same service. KEDA scales the Container Apps job from
the dedicated subscription.

The subscription uses at-least-once delivery, dead-lettering on expiration, and
a finite maximum delivery count. The worker renews the message lock for the
entire configured explanation timeout; the five-minute broker lock alone must
not be assumed sufficient for GNNExplainer.

## 7. Request contract

Event type:

```text
gnn.explanation.requested
```

Schema version 1 body:

```json
{
  "event_type": "gnn.explanation.requested",
  "schema_version": 1,
  "request_id": "gnnexp:t1:n13881:gnn-v2-20260911-t1-ab12cd34",
  "nomination_id": 13881,
  "tenant_id": 1,
  "gnn_model_version": "gnn-v2-20260911-t1-ab12cd34",
  "gnn_scoring_policy_version": 4,
  "embedding_as_of": "2026-09-11",
  "graph_snapshot_id": "gnn-graph-v2-20260911-t1-ab12cd34",
  "request_reason": "MEDIUM_OR_HIGHER",
  "source_message_id": "nomination-submitted-message-id",
  "requested_at": "2026-09-11T18:00:00Z"
}
```

Service Bus `MessageId` equals `request_id`:

```text
gnnexp:t<tenant_id>:n<nomination_id>:<gnn_model_version>
```

The message is a pointer, not a data transfer. It contains no graph, model
weights, description, name, email address, or other avoidable PII.

## 8. Explanation lifecycle and idempotency

The nested explanation object uses these states:

```text
NOT_REQUESTED
REQUESTED → RUNNING → COMPLETED
     |          |
     +----------+→ FAILED
```

`FAILED` includes a stable `reason` and a `retryable` flag. A publish failure is
recoverable by reconciliation. A permanent worker failure remains terminal for
that request unless an operator explicitly requeues it.

The database claim, not Service Bus duplicate detection alone, is authoritative.
The worker conditionally changes the matching request from `REQUESTED` or an
eligible retry state to `RUNNING` and writes:

- `attempt_id`;
- `attempt_count`;
- `started_at`; and
- `worker_version`.

A duplicate delivery behaves as follows:

- matching `COMPLETED`: complete the message without recomputation;
- matching fresh `RUNNING`: complete the duplicate because another worker owns it;
- stale `RUNNING`: reclaim with a new attempt ID after the configured stale interval;
- a newer persisted model/request: log the old request as superseded and complete
  it without changing the current result;
- the same request ID with different model/snapshot metadata: reject as a
  permanent contract mismatch; and
- absent decision or explanation request: retry briefly for ordering, then fail permanently.

No separate explanation or processed-event table is required. Conditional JSON
updates on the canonical result provide request ownership and idempotency.

## 9. Artifact contract

The worker downloads only the immutable bundle named by the request:

```text
gnn/tenant_<tenant_id>/<model_version>/
  manifest.json
  graph_snapshot.pt
  serving/
    encoder.pt
    decoder.pt
```

The top-level `manifest.json` is required. It is the trust index for:

- tenant ID;
- artifact and feature schema versions;
- selected architecture;
- model version and graph snapshot ID;
- training-policy identity;
- relative artifact paths, byte sizes, and SHA-256 hashes; and
- the relation and feature contracts needed to rebuild the model safely.

The worker does **not** read `candidates/<architecture>/metrics.json`. Candidate
metrics are offline comparison and audit evidence; they are neither an
explanation input nor an inference dependency.

All `.pt` files are loaded with restricted `weights_only=True` deserialization.
The worker rejects:

- an unsupported manifest or feature schema;
- tenant, model-version, or snapshot mismatch;
- an MLP selection presented as a serving GNN;
- an unsupported selected graph architecture;
- a missing artifact;
- size or hash mismatch;
- non-tensor/non-primitive snapshot content; and
- cross-tenant nodes, edges, or mappings.

The initial worker supports GraphSAGE, GCN-family `GraphConv`, and GATv2 because
any one may be the operational winner.

## 10. Score reproduction gate

Before explanation, the worker reconstructs the exact scoring case from:

- the immutable graph snapshot;
- selected serving encoder and decoder;
- the version-matched nominator and beneficiary embeddings stored in
  `dbo.GNN_UserEmbeddings`;
- stored preprocessing state;
- nominator and beneficiary IDs;
- candidate nomination attributes; and
- the model and snapshot identifiers persisted with the original GNN result.

The gate has two checks:

1. **Serving reproduction:** apply the serving decoder to the same versioned SQL
   embeddings and nomination features used by `integrity-check`, then compare
   that probability with `GnnResultJson`.
2. **Graph reconstruction:** run the serving encoder over `graph_snapshot.pt`,
   recover the endpoint embeddings, apply the decoder, and compare those
   embeddings and probability with the serving-reproduction values.

The first check proves the live score can be reproduced. The second proves the
graph supplied to GNNExplainer is the graph that produced the stored
representations. If either difference exceeds its configured tolerance, the
worker persists `FAILED/SCORE_REPRODUCTION_MISMATCH` and emits no evidence.

This gate prevents an explanation of a model, graph, preprocessing state, or
nomination representation different from the one that produced the verdict.

## 11. GNNExplainer execution

The worker runs multiple deterministic seeds against the selected architecture.
The initial algorithm is GNNExplainer; the persisted `method` is explicit so a
future algorithm can coexist without silently changing semantics.

The explainer produces separate ranked evidence for:

- relationships/edges influencing the nominator and beneficiary representation;
- historical graph nodes participating in those relationships; and
- candidate nomination features consumed by the decoder.

Raw optimizer masks are not user-facing evidence. The formatter resolves tensor
indexes through the snapshot mappings and emits bounded, tenant-scoped business
objects. Evidence is limited to configured top counts and minimum importance.

Quality gates include:

- score reproduction;
- fidelity when retained evidence is applied;
- fidelity degradation when important evidence is removed;
- rank stability across seeds; and
- bounded runtime and memory.

Evidence that fails the locked fidelity or stability minimum is not displayed as
a reliable explanation. The result is persisted as `FAILED` with a specific
quality reason rather than as a misleading `COMPLETED` explanation.

## 12. Completed result contract

Example `GnnResultJson.explanation`:

```json
{
  "method": "GNNEXPLAINER",
  "schema_version": 1,
  "status": "COMPLETED",
  "request_id": "gnnexp:t1:n13881:gnn-v2-20260911-t1-ab12cd34",
  "attempt_count": 1,
  "architecture": "graphsage",
  "model_version": "gnn-v2-20260911-t1-ab12cd34",
  "graph_snapshot_id": "gnn-graph-v2-20260911-t1-ab12cd34",
  "started_at": "2026-09-11T18:01:00Z",
  "completed_at": "2026-09-11T18:01:42Z",
  "duration_ms": 42000,
  "score_reproduction": {
    "persisted_probability": 0.7312,
    "serving_probability": 0.7312,
    "graph_reconstructed_probability": 0.7311,
    "serving_absolute_difference": 0.0,
    "graph_absolute_difference": 0.0001,
    "probability_tolerance": 0.001,
    "embedding_tolerance": 0.0001,
    "passed": true
  },
  "quality": {
    "seed_count": 3,
    "fidelity": 0.87,
    "stability": 0.81,
    "passed": true
  },
  "top_relationships": [
    {
      "relation": "nominates",
      "source_type": "user",
      "source_id": 435,
      "target_type": "nomination",
      "target_id": 13842,
      "importance": 0.19
    }
  ],
  "top_features": [
    {
      "scope": "candidate_nomination",
      "name": "CategoryRelativeAmountRobustZScore",
      "value": 2.14,
      "importance": 0.16
    }
  ]
}
```

Importance values are algorithm-specific attribution strengths, not percentage
points of fraud probability. The UI must not label them as SHAP values or imply
that they add to the GNN score.

## 13. Database concurrency contract

Every worker update must match:

- `NominationId`;
- `TenantId`;
- `GnnResultJson.model_version`;
- `GnnResultJson.graph_snapshot_id`; and
- `GnnResultJson.explanation.request_id`.

The worker uses `JSON_MODIFY` with `JSON_QUERY` to replace only
`$.explanation`. It updates `UpdatedAt` but preserves `CreatedAt` and `ScoredBy`.
Service attribution is written to `dbo.Nomination_Logs` as
`svc:integrity-check-extension`.

The update may not modify RF, Graph Analytics, Semantic, composite, routing,
review, nomination-status, or training-disposition data. A zero-row conditional
update is a concurrency or contract failure, never a successful no-op unless a
fresh read proves the identical request is already `COMPLETED`.

`integrity-check` retries preserve an existing matching `RUNNING` or `COMPLETED`
object. They may not replace it with a newly generated `REQUESTED` placeholder.

## 14. Failure and message settlement policy

| Failure | Persisted result | Settlement |
|---|---|---|
| Duplicate already completed | unchanged | Complete |
| Fresh concurrent `RUNNING` claim | unchanged | Complete duplicate |
| Request superseded by a newer persisted model | unchanged | Complete |
| SQL, Blob, or network timeout | non-terminal attempt detail/log | Abandon for retry |
| Artifact temporarily absent | non-terminal until final delivery | Abandon for retry |
| Maximum delivery reached | `FAILED`, stable reason | Dead-letter |
| Invalid JSON/schema/event type | `FAILED` when request can be identified safely | Dead-letter |
| Tenant ownership mismatch | no cross-tenant detail persisted | Dead-letter and security log |
| Model/snapshot/hash/schema mismatch | `FAILED`, stable reason | Dead-letter |
| Score reproduction mismatch | `FAILED/SCORE_REPRODUCTION_MISMATCH` | Dead-letter |
| Explainer timeout/resource limit | retry until maximum delivery | Abandon, then dead-letter |
| Fidelity/stability below minimum | `FAILED`, quality reason | Complete |
| Successful explanation | `COMPLETED` | Complete |

Stable failure reasons include:

```text
INVALID_MESSAGE
UNSUPPORTED_SCHEMA
TENANT_MISMATCH
DECISION_NOT_FOUND
REQUEST_MISMATCH
ARTIFACT_UNAVAILABLE
ARTIFACT_HASH_MISMATCH
MODEL_VERSION_MISMATCH
GRAPH_SNAPSHOT_MISMATCH
UNSUPPORTED_ARCHITECTURE
SCORE_REPRODUCTION_MISMATCH
EXPLANATION_TIMEOUT
INSUFFICIENT_FIDELITY
INSUFFICIENT_STABILITY
INTERNAL_ERROR
```

## 15. Configuration ownership

Tenant policy remains in `dbo.GNNScoringPolicies`:

- `ExplanationEnabled` is the activation switch;
- `ExplanationMinimumRisk` is the request threshold; and
- explanation algorithm parameters belong in an `explanation` object within
  `ConfigurationJson`, not in Terraform-managed container environment variables.

The exact initial values for reproduction tolerance, explainer epochs and seeds,
top evidence counts, minimum fidelity, minimum stability, and per-request timeout
must be locked after offline fixture testing and before tenant activation.

Environment variables are limited to infrastructure bindings such as Service Bus
namespace/subscription, storage account/container, SQL database, managed-identity
client ID, application version, and process-level concurrency.

## 16. Logging and monitoring

Persistent nomination logs use:

```text
service = integrity-check-extension
logger  = integrity_check_extension.gnn_explainer
```

Required messages:

- `GNN explanation started`
- `GNN explanation completed`
- `GNN explanation failed`
- `GNN explanation request already completed — skipping`
- `GNN explanation request dead-lettered`

Details include request ID, attempt, architecture, model version, snapshot ID,
duration, reproduction difference, fidelity, stability, evidence counts, and a
stable failure reason. They do not include raw descriptions, names, emails,
model tensors, or graph payloads.

The Nomination Logs **Integrity check only** filter includes both namespaces:

```sql
logger LIKE 'integrity[_]check.%'
OR logger LIKE 'integrity[_]check[_]extension.%'
```

Operational telemetry includes queue depth and age, delivery attempts, duration
percentiles, completion/retry/dead-letter rates, stale claims, artifact failures,
score mismatches, and fidelity/stability distributions.

## 17. Security

The worker uses managed identity and least privilege. It requires:

- Service Bus receive permission on the explanation subscription;
- Blob read permission for GNN bundles;
- SQL read access to the nomination and canonical integrity result;
- SQL permission to update only the nested GNN explanation and `UpdatedAt`; and
- insert permission for nomination logs.

It must not have permission to update nomination status, routing, human review,
training disposition, or another engine result. The tenant ID in the message is
untrusted and must be verified through nomination ownership before artifact or
history access.

## 18. UI contract

HRBP Review and Model Analysis display the persisted explanation asynchronously.
They do not call the extension service directly.

Supported states are:

- `GNN explanation not requested`
- `GNN explanation pending`
- `GNN explanation in progress`
- `GNN explanation completed`
- `GNN explanation unavailable: <concise reason>`

Completed evidence appears under **GNN Model (GNNExplainer) Breakdown**.
Relationships and candidate features are separate. Fidelity, stability, model
version, snapshot, and architecture appear in an expandable technical section.
No explanation state may imply that nomination routing is waiting.

## 19. Delivery phases

Implementation status as of 2026-09-11: E1 and E2 are implemented for the
sandbox environment. Tenant `ExplanationEnabled` must remain false for deployment;
the E1/E2 worker fails closed with `EXPLANATION_ENGINE_NOT_DEPLOYED` if a request
is introduced before E3. Production and development infrastructure remain
intentionally unprovisioned until the sandbox path is validated.

### Phase E1 — service and contract foundation

- scaffold the project and dispatcher;
- add Service Bus subscription/filter and managed identity;
- exclude explanation requests from `email-processor`;
- implement validation, lock renewal, settlement, and conditional claims;
- implement nomination-scoped logs; and
- deploy with `ExplanationEnabled=false`.

### Phase E2 — artifact loading and score reproduction

- validate the top-level manifest and artifact hashes;
- load snapshot, selected encoder, and decoder safely;
- support GraphSAGE, GCN-family, and GATv2;
- reconstruct the scored case; and
- enforce probability reproduction tolerance.

### Phase E3 — structured explanation

- implement seeded GNNExplainer;
- compute fidelity and stability;
- format bounded tenant-safe evidence; and
- atomically persist completed and failed results.

### Phase E4 — operational completion

- implement stale-request reconciliation;
- add dead-letter monitoring and runbook;
- add HRBP Review and Model Analysis states;
- exercise retries, duplicate delivery, and rollback; and
- publish operational dashboards and alerts.

### Phase E5 — tenant validation and activation

- train and select a valid tenant GNN;
- manually request explanations for representative scored nominations;
- lock quality thresholds and runtime limits;
- review evidence with data-science and HRBP users; and
- enable the tenant only after acceptance criteria pass.

## 20. Acceptance criteria

The extension may be enabled for a tenant only when:

1. scoring, persistence, and routing complete without waiting for explanation;
2. requests are deterministic, tenant-scoped, versioned, and contain no PII;
3. the email processor cannot receive explanation events;
4. duplicate and concurrent deliveries are idempotent;
5. the exact selected architecture, model, snapshot, and hashes are validated;
6. the persisted probability is reproduced within the locked tolerance;
7. low-fidelity or unstable evidence is never presented as reliable;
8. only `GnnResultJson.explanation` and `UpdatedAt` are mutated;
9. retries cannot erase a completed explanation;
10. GraphSAGE, GCN-family, and GATv2 winner bundles are supported;
11. transient, permanent, and security failures settle messages correctly;
12. persistent logs appear under the integrity-only filter;
13. the UI presents asynchronous state without implying routing is blocked;
14. least-privilege and cross-tenant tests pass;
15. dead-letter monitoring, reconciliation, and rollback are exercised; and
16. representative real tenant scores produce useful, stable explanations.

## 21. Decisions to lock before Phase E3 activation

The architecture and contracts above are fixed. These numeric operational
values remain to be measured with fixture artifacts and then real tenant scores:

1. probability reproduction tolerance;
2. explainer epochs and seed count;
3. maximum relationships, nodes, and features persisted;
4. minimum evidence importance;
5. minimum fidelity and stability;
6. per-request timeout and memory limit;
7. stale `RUNNING` claim interval;
8. maximum Service Bus delivery count; and
9. stale `REQUESTED` reconciliation interval.

These values do not block Phase E1 or E2. They do block tenant activation.
