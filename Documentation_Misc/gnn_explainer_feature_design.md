# GNNExplainer Feature Design

**Status:** Implementation design; asynchronous foundation and evidence UI exist,
but GNNExplainer attribution execution is not implemented.

**Last updated:** 2026-09-26

**Applies to:** `integrity-check`, `integrity-check-extension`, immutable GNN
bundles, `dbo.IntegrityDecisionResults`, HRBP Review Queue, Nomination Logs,
and Nomination/Model Analysis.

This document defines the explanation feature and the remaining E3 work. The
[extension design](integrity_check_extension_design.md) defines the service
boundary; the [GNN v4 design](gnn_v4_shared_encoder_multi_head_design.md)
defines the model being explained. This documentation does not deploy code,
change tenant policies, activate a model, or authorize corpus changes.

## 1. Purpose and boundaries

For a persisted nomination, explain which historical relationships and model
inputs influenced the selected GNN's prediction. Explain the prediction that
actually occurred, not a new prediction made with today's graph or model.

GNNExplainer is asynchronous and non-routing. It must not change the GNN score,
probability, availability, composite decision, nomination status, human review,
or training labels. Failure to explain does not mean failure to score.

It must remain independent of Graph Analytics. A deterministic Ring finding is
not an explanation input, a target label, or evidence that the learned GNN
must agree. A successful explanation may help investigate that disagreement;
it cannot repair a low GNN score or prove that fraud occurred.

The feature does not require a new model architecture, a new explanation table,
synthetic corpus reseeding, or retraining solely to explain an already compatible
bundle. Incompatible historical artifacts must be reported explicitly, not
silently reconstructed from a newer training run.

## 2. Current implementation and known gaps

| Area | Current state | Remaining work |
|---|---|---|
| Request producer | Deterministic, nomination-scoped asynchronous requests | End-to-end canonical-result coverage |
| Trigger | Eligible requests are automatic; legacy enable flag is ignored | Preserve explicit skipped reasons |
| Artifact loader | Validates manifest identities, hashes, sizes, and restricted tensor loading | Validate actual published v4 bundles end to end |
| Reconstruction | Encoder, decoder, scalers, calibration, and versioned embeddings are available | Correct persisted-probability field handling and verify real scored cases |
| Attribution | Dispatcher raises `EXPLANATION_ENGINE_NOT_DEPLOYED` after successful reproduction | Differentiable wrapper, masks, optimization, and evidence formatting |
| Persistence | Conditional claims and explanation-only update helper exist | Successful completion, ownership-safe retries, and quality results |
| UI | Detailed GNN evidence and explicit explanation lifecycle are visible | Render and validate completed structured attribution |

### 2.1 Canonical probability defect

`inference/decision_contract.py` persists `model_probability`. The extension's
`reproduction.py` currently reads `fraud_prob`, which is the internal scoring
field. For a v4 overall request, the dispatcher passes the persisted JSON
without translating that field. Reproduction can therefore fail with
`STORED_GNN_PROBABILITY_MISSING` before reaching the attribution placeholder.

The worker must consume the canonical result contract. Tests must pass a real
`decision_contract.gnn_result(...)` payload through the request-context and
reproduction path, not only construct internal-result dictionaries. Preserve
existing v3 specialist compatibility explicitly; never default a missing
probability to zero.

### 2.2 Dependency compatibility

The current requirement permits PyG versions from 2.5 onward. PyG 2.6.1's
GNNExplainer explicitly rejects heterogeneous dictionaries, whereas 2.7.0
implements heterogeneous masks. The development installation inspected on
2026-09-26 is PyG `2.8.0.post1` with Torch `2.12.0+cpu`; this does not establish
the version in the deployed image.

Pin and test an exact Torch/PyG combination that supports the actual graph and
all serving architectures. Do not infer deployment compatibility from a broad
dependency range or from local imports succeeding. See the primary sources
in section 13.

## 3. Request eligibility and explanation targets

`ExplanationEnabled` is a legacy SQL/API compatibility field, not a runtime
activation switch. Do not reintroduce an enable toggle or a UI technical-details
gate. Apply the same explanation lifecycle presentation across tenants.

Automatic requests retain the existing checks:

- the GNN model is available;
- the result meets `ExplanationMinimumRisk`, defaulting to `MEDIUM`;
- the result identifies the immutable model and graph snapshot; and
- any legacy specialist request identifies an available decisive specialist.

Skipped requests persist `NOT_REQUESTED` with the actual reason, such as
`BELOW_TRIGGER_RISK`, `MODEL_UNAVAILABLE`, or missing reproduction metadata.
Existing historical `FEATURE_DISABLED` records retain their original meaning.

For v4, the initial execution target is `OVERALL`, whose probability determines
the GNN routing score. The v4 design also calls for explaining the highest
active material pattern head alongside the overall head. That head-scoped
request and result contract remains pending; implement it as a separate target,
not as a replacement for the overall explanation or as a v3 specialist.

Only `ACTIVE` pattern heads may be explained as live pattern predictions.
Diagnostic-only heads remain explicitly diagnostic. Additional requested head
explanations are planned analysis functionality, not an existing endpoint.

A score-zero nomination normally falls below the automatic trigger. Inspecting
it requires an explicitly authorized diagnostic invocation or policy change;
showing its feature inputs is not evidence that GNNExplainer ran.

## 4. Inputs and exact reproduction

The source of truth is the nomination's persisted `GnnResultJson`, together with
the immutable bundle named by its model/snapshot identity:

```text
ml-models/tenant_<tenant_id>/awards/gnn/<model_version>/
  manifest.json
  graph_snapshot.pt
  serving/encoder.pt
  serving/decoder.pt
```

Use the manifest's artifact descriptors rather than assuming optional files
exist. Calibration and preprocessing metadata are currently carried by the
serving artifacts. Candidate comparison metrics are not inference inputs.

Validate tenant ownership, request identity, architecture, feature schema,
ordered feature columns, artifact hashes, and endpoint mappings. Load tensors
with restricted deserialization. Do not fall back to the latest serving bundle.

Reproduction has two checks:

1. **Serving reproduction:** the version-matched SQL endpoint embeddings,
   original nomination inputs, decoder, and calibration reproduce the persisted
   probability within the recorded tolerance.
2. **Graph reproduction:** reconstruct the endpoint embeddings from the
   immutable snapshot and encoder, then reproduce the same calibrated output.

Prefer the persisted ordered `feature_inputs` when present, validating them
against the artifact's column/scaler contract. Stored causal feature values must
be used, not recomputed from today's nomination history. Any remaining nomination
attributes reconstructed from SQL must be proven consistent with scoring time;
edits must cause an explicit mismatch rather than explain a different case.

Record persisted, serving-reproduced, and graph-reconstructed probabilities,
absolute differences, embedding difference, and tolerances. The existing
defaults are probability tolerance `0.0005` and embedding tolerance `0.00001`;
these are implementation defaults, not experimentally established guarantees.
Respect the precision of the persisted probability.

## 5. Explainable model wrapper

For new `gnn-live-causal-encoder-v1` bundles, consult
[Normal live GNN encoding](gnn_live_encoding_design.md). The original prediction
uses scoring-time causal graph inputs, not solely the weekly snapshot. The
reproduction and attribution work must preserve/reconstruct that graph and
the refreshed endpoint embeddings before it can claim score fidelity. The
snapshot-only reconstruction below describes historical decoder-only bundles.

The live worker scores cached embeddings. Applying GNNExplainer directly to
that decoder would not expose historical message-passing edges. The explanation
worker must reconstruct this complete differentiable path:

```text
immutable graph features and typed edges
                  |
           frozen graph encoder
                  |
       nominator / beneficiary embeddings
                  |                         persisted target feature vector
                  +-------------------------------+
                                  |
                         frozen target decoder
                                  |
                     target-specific calibration
                                  |
                      original calibrated prediction
```

Keep model weights frozen and dropout disabled. Optimize explanation masks,
not trained model weights. Preserve gradients through encoder, decoder, and
calibration: the attribution path cannot use the reproduction helper's detached
NumPy embeddings or run its forward pass under `torch.no_grad()`.

The wrapper must select the correct decoder and calibration for `OVERALL` or
an active pattern head. Pattern targets use their recorded feature slice and
their own calibration; no head may borrow the overall calibration.

Validate GraphSAGE, GCN-family `GraphConv`, and GATv2 independently. Do not convert
the graph to a different topology or substitute an architecture just to make
an explainer API convenient.

## 6. Attribution scope and interpretation

### 6.1 Historical graph evidence

Optimize typed edge and graph-node feature masks through the frozen encoder.
Resolve tensor indices through snapshot mappings into tenant-scoped users,
historical nomination IDs, and relationships. Preserve direction and relation
type. Forward and reverse computational edges must not be counted as two
independent business nominations when summarizing support.

Start with the verified snapshot. A smaller computation subgraph is an optional
performance optimization, not a prerequisite or a new detection window. Its
unmasked output must reproduce the full graph, including neighborhood aggregation
and category-node dependencies; an arbitrary endpoint neighborhood can change
the model being explained.

### 6.2 Candidate and live causal features

The target vector enters the decoder separately from graph-node features.
Ordinary PyG graph masks do not automatically attribute a feature tensor passed
as an extra model argument. Add explicit target-feature masking to the
explanation adapter and test its gradient/optimization path.

The mask baseline is part of the contract. For a standardized target vector,
zero means the artifact's scaler mean, not a raw count or amount of zero.
Persist the baseline semantics and before-scaler/model-input values.

Live causal counts can include nominations created after the weekly snapshot.
The explanation may attribute those count inputs, but must not claim their
supporting nominations were historical encoder edges unless those edges exist
in the immutable snapshot. Keep the original causal counts fixed while testing
historical graph masks; silently recomputing them would change the live model's
input construction.

### 6.3 What importance means

Use model-prediction explanation, not investigator labels or Graph Analytics
findings as the optimization target. Explain a low prediction as low when it is
explicitly inspected; do not optimize toward a desired fraud outcome.

Mask importance is algorithm-specific evidence strength. It is not a signed SHAP
contribution, a percentage-point change in fraud probability, or proof of fraud.
Do not add mask scores together to reconstruct the GNN score. Any claimed
direction or probability change needs an explicit perturbation measurement.

Preserve separate evidence groups for historical relationships, historical node
features, and candidate features. A strong category or amount attribution must
not be described as evidence of a nomination ring.

## 7. Quality and resource measurements

Measure explanation quality separately from model admission. This feature does
not change PR-AUC selection, admission margins, head states, or training labels.

For the exact target, record:

- unmasked reproduced probability;
- probability with retained evidence and its absolute deviation;
- probability with highlighted evidence removed;
- the target class and whether removal weakens support for that class;
- agreement of ranked evidence across deterministic seeds; and
- duration, epochs, seeds, node/edge counts, and computational scope.

For low-risk predictions, removing evidence need not lower fraud probability;
it may raise it. Fidelity semantics must identify the explained target rather
than assume every explanation supports a fraud alert. A single small probability
change is not sufficient proof of useful attribution.

Define stability using a reproducible top-k overlap/rank metric, including tie
and empty-evidence handling. Name the metric; do not emit an unexplained scalar.
Define any summary fidelity value alongside the underlying probability tests.

Benchmark seeds, epochs, evidence limits, quality criteria, timeout, and memory
on compatible fixture bundles and an actual scored v4 nomination before choosing
operational defaults. No new numeric thresholds are approved by this document.
Respect the worker's broker-lock renewal and stale-claim intervals; a request
deadline must be enforced, not merely described in configuration.

If bounded execution or quality requirements cannot be met, persist an explicit
failure and retain the original score and inputs. Never present empty evidence
as a successful claim that nothing mattered.

## 8. Lifecycle, persistence, and result contract

```text
NOT_REQUESTED (reason)
REQUESTED -> RUNNING -> COMPLETED
     |          |
     +----------+----> FAILED (reason)
```

Requests remain idempotent and tenant/model/snapshot scoped. Current v4 overall
requests use the existing version-1 message; version-2 messages represent legacy
v3 specialists. A future head-targeted message must have an explicit compatible
schema and head-qualified identity, without colliding with overall requests.

Only replace `GnnResultJson.explanation` and update its row timestamp. Match
tenant, nomination, model, snapshot, request ID, and owned running attempt before
writing. A stale worker must not overwrite a newer claim or completed result.
The current claim/update helper needs attempt-ownership coverage for this case.

Current fields `method`, `status`, `reason`, `request_id`, and timestamps remain
compatible. A completed result will additionally identify:

- explanation schema/worker/algorithm version and exact dependency versions;
- target head, architecture, model, snapshot, and feature schema;
- effective configuration and feature-mask baseline;
- reproduction measurements;
- fidelity measurements and named stability metric;
- computational scope and execution duration;
- ranked `top_relationships` with mapped endpoints and supporting nomination IDs;
- ranked `top_features` with scope, feature name, raw/scaled values, and importance;
- optional mapped node evidence; and
- a factual summary with explicit explanation limitations.

Preserve top-level `fidelity`/`stability` compatibility if the existing UI uses
them, but define their formulas in the detailed quality object. Serialize only
finite JSON numbers; tensors, masks, and non-finite values must not leak into
the decision row. Do not persist the entire graph or optimizer state.

For multiple head explanations, define the nested per-head result/update
contract before publishing head requests, preserving the overall result and
any concurrently completed head. The current single explanation object must
not be overwritten repeatedly by independent head workers.

## 9. UI and logging

HRBP Review Queue, Nomination Logs, and Nomination/Model Analysis use the same
evidence semantics. GNN details are always accessible, without a technical-details
permission or display gate.

Show the recorded model and target probability, active/diagnostic head states,
causal input table, complete feature vector when recorded, and explanation
status. Input values remain visible even when explanation fails; label them as
inputs, not attribution.

| Recorded state | User-facing meaning |
|---|---|
| `NOT_REQUESTED` | GNNExplainer not called, with the stored reason |
| `REQUESTED` | Pending asynchronous explanation |
| `RUNNING` | Explanation in progress |
| `COMPLETED` | Structured evidence and measured quality |
| `FAILED` | Explanation unavailable, with the failure reason |
| Missing historical status | Not recorded; no explanation is inferred |

This is lifecycle parity with RF/SHAP, not an assertion that the algorithms
produce the same attribution units. Keep small nonzero probabilities visible.
Do not turn an unavailable or diagnostic head into a zero-risk claim.

Nomination Logs retain the evidence recorded for that scoring event, not today's
serving architecture. Log request, start, completion/failure, target identity,
reproduction/quality summaries, and duration. Avoid raw descriptions, emails,
names, full graph tensors, and unbounded evidence dumps.

## 10. Configuration ownership

Keep the existing request threshold in `dbo.GNNScoringPolicies.ExplanationMinimumRisk`.
Explanation algorithm/quality parameters belong under `ConfigurationJson.explanation`.
Record the effective configuration for reproducibility; changes during a request
must not silently change its explanation settings.

Existing keys are `probability_reproduction_tolerance` and
`embedding_reproduction_tolerance`. Proposed execution settings cover epochs,
deterministic seeds, mask regularization/baseline, evidence limits, quality
measurements, and per-request time/resource budgets. Their exact schema and
defaults must be validated during E3; they are not currently implemented simply
because they appear here.

Infrastructure/concurrency limits remain process configuration. Graph Analytics
detector windows and GNN training/history windows retain their own ownership.
The explainer does not introduce another detection window or use the Graph Ring
window to replace the GNN snapshot.

No new SQL table, migration, tenant-specific enable flag, or Terraform service is
required by this design. Any necessary contract or permission change found during
implementation must be reported explicitly before deployment.

## 11. Implementation sequence

| Step | Deliverable | Evidence required |
|---|---|---|
| E3.1 Canonical reproduction | Fix canonical probability handling; pin/test dependencies; verify persisted features/calibration | Actual decision-contract payload and one actual v4 scored case reproduce |
| E3.2 Differentiable wrapper | Frozen encoder plus target decoder/calibration; connected graph and target-feature masks | Unmasked parity and nonzero gradients for relevant masks, across all three architectures |
| E3.3 Attribution and quality | Seeded optimization, bounded execution, mapped evidence, fidelity/stability | Useful controlled fixtures, repeatability, no fabricated attribution or cross-tenant evidence |
| E3.4 Completion and UI | Ownership-safe completed results, consistent rendering, explicit failure states | Concurrency/retry tests and both HRBP and log rendering tests |
| E3.5 Existing-corpus validation | Explain representative tenant-5 nominations without reseeding | Real bundle/decision identity, measured runtime, unchanged scoring and routing |

Complete and validate the overall target first. Preserve the v4 goal of overall
plus highest active material pattern explanation as a subsequent head-contract
step, not as a reason to delay the first verified overall explanation.

Development validation on tenant 5 does not create a permanent tenant-5-only
feature. Do not silently impose new activation rules on other tenants.

## 12. Acceptance tests

Automated and artifact-backed checks must prove:

1. A canonical persisted v4 probability is read correctly; absence is not zero.
2. All supported architectures reproduce their artifacts, embeddings, features,
   and target-specific calibrated output.
3. Weights remain unchanged; relevant graph and target-feature masks receive gradients.
4. Candidate features are attributed even though they bypass the encoder.
5. Graph IDs, relation direction, reverse edges, and supporting nomination IDs
   map correctly within the requesting tenant.
6. Historical graph evidence is distinguished from post-snapshot causal inputs.
7. Any reduced graph reproduces the unmasked full-graph computation.
8. Wrong tenant/model/snapshot/hash/feature/head identities fail explicitly.
9. Probability precision, empty evidence, invalid numbers, zero-gradient cases,
   instability, timeout, and low-fidelity outcomes are handled honestly.
10. Duplicate delivery and stale workers cannot overwrite another owned attempt,
    completed explanation, or newer decision.
11. Successful evidence is persisted once and appears in HRBP Review and logs;
    missing/skipped/failed evidence is never shown as completed attribution.
12. Only the explanation and update timestamp change; scores, routing, head
    states, human decisions, and training dispositions remain untouched.

The current automatic-request producer can enqueue work before E3 exists. A
worker may presently fail during reproduction or at the explicit
`EXPLANATION_ENGINE_NOT_DEPLOYED` placeholder. Removing enable gates is not
proof that attribution works; deployment readiness requires E3 acceptance.

## 13. Primary references

- [GNNExplainer paper](https://arxiv.org/abs/1903.03894): explains model predictions
  using compact graph/feature evidence, not investigator adjudication.
- [PyG 2.7.0 GNNExplainer source](https://pytorch-geometric.readthedocs.io/en/2.7.0/_modules/torch_geometric/explain/algorithm/gnn_explainer.html): heterogeneous masks and model forward integration.
- [PyG 2.6.1 GNNExplainer source](https://pytorch-geometric.readthedocs.io/en/2.6.1/_modules/torch_geometric/explain/algorithm/gnn_explainer.html): explicit heterogeneous-graph rejection in this version.

The two-path wrapper, canonical-result integration, persistence semantics, and
implementation sequence above are application-specific design decisions; they
are not claims that the library implements those integration steps automatically.
