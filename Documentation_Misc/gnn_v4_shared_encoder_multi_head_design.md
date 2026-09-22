# GNN v4 Shared-Encoder Multi-Head Design

**Status:** Initial implementation in code; not yet activated or validated against tenant 5  
**Owner:** Integrity modeling  
**Applies to:** `fraud-analytics-job`, `integrity-check`,
`integrity-check-extension`, `dbo.GNNScoringPolicies`,
`dbo.GNN_UserEmbeddings`, `dbo.IntegrityComponentStatus`,
`dbo.IntegrityDecisionResults`, and the administrative integrity UI  
**Last updated:** 2026-09-22

## Tenant-5 activation sequence

Migration `0065_tenant5_gnn_shared_multi_head` is the explicit policy switch
for Synthetics Inc. (`TenantId = 5`) only. Because the schema-migration workflow
runs automatically on a push that changes `schema-migration/**`, release the
v4-capable analytics job, integrity-check worker, extension, API, and frontend
first. Publish/run migration 0065 only after those deployments succeed. Do not
push all six deployment targets at once and assume workflow ordering.

Migration 0065 requires the exact synthetic tenant and an active v3 policy,
rejects an existing draft or disabled training, preserves the tenant's model,
window, artifact, score-routing, inference, and explanation settings, and
creates a new active policy version. It retires rather than deletes the v3 row.
It does not change the current serving model pointer; the next analytics run
must train and pass v4 admission before a v4 model serves.

After migration, verify the active policy with:

```sql
SELECT PolicyId, PolicyVersion, Status, TrainingEnabled, InferenceEnabled,
       JSON_VALUE(ConfigurationJson, '$.schema_version') AS SchemaVersion,
       JSON_VALUE(ConfigurationJson, '$.serving_mode') AS ServingMode
FROM dbo.GNNScoringPolicies
WHERE TenantId = 5
ORDER BY PolicyVersion DESC;
```

Expect exactly one `ACTIVE` row with schema version `4` and serving mode
`shared_encoder_multi_head`. Then run the fraud-analytics job and inspect its
tenant-5 attempt and immutable manifest. A successful migration is not, by
itself, evidence that a v4 candidate passed admission or became the serving
model.

## 1. Decision

GNN v4 replaces the independently trained scenario-specialist model with one
tenant-scoped graph encoder and several jointly trained output heads.

```text
                         Shared graph encoder
                                  |
             +--------------------+--------------------+
             |                    |                    |
       Overall integrity      Pattern heads       User embeddings
            head           (multi-label evidence)   for live scoring
                                  |
           +-----------+----------+----------+-------------------+
           |           |          |          |                   |
       RECIPROCAL    RING    TEMPORAL_BURST  SUPER_NOMINATOR  ...
```

GraphSAGE, GCN-family, and GATv2 remain candidate encoder architectures. They
compete once for the tenant-wide shared model. They do not compete separately
inside every pattern family.

The overall head produces the one GNN probability consumed by integrity
fusion. Pattern heads produce typed supporting evidence. Pattern probabilities
do not become additional engine votes and are not combined with the overall
probability through an undocumented maximum or score bonus.

This design reuses the existing Synthetics Inc. corpus. It does not require a
new corpus, additional users, or additional nominations before implementation.

## 2. Why v4 replaces the v3 specialist boundary

GNN v3 trained an independent encoder and decoder for every pattern family.
With the current corpus, each model received only 30 pattern-positive training
targets and was selected with 10 positives in each temporal evaluation period.
That made architecture selection unstable and prevented patterns from sharing
useful representation learning.

V4 preserves the useful parts of v3:

- explicit, model-neutral pattern labels;
- per-pattern probabilities, metrics, thresholds, and explanations;
- temporal evaluation;
- abstention when a pattern head lacks evidence;
- engine independence from Graph Analytics; and
- tenant-isolated artifacts and serving state.

V4 removes:

- one encoder per pattern;
- one architecture competition per pattern;
- separate user-embedding generations per pattern;
- maximum-probability aggregation across separately trained specialists; and
- independent specialist artifact lifecycles.

The term **specialist** may remain in user-facing discussion for a pattern
head, but the persisted and implementation vocabulary is **pattern head**.
This avoids implying that every head owns a complete model.

## 3. Relationship to earlier GNN versions

V2 remains the single-binary-model rollback contract. V3 remains an immutable
record of the independently trained specialist experiment and its artifacts.
V4 becomes the forward design after approval.

V4 preserves the following guarantees:

- tenant isolation;
- causal graph construction using only information available before the
  target nomination;
- target-edge exclusion from message passing;
- immutable and hashed artifacts;
- version-matched user embeddings and decoder artifacts;
- atomic component activation;
- temporal architecture selection and final testing;
- asynchronous explanations; and
- exactly one GNN verdict in cross-engine routing.

The source-adapter and canonical-feature boundaries remain governed by
`Documentation_Misc/integrity_analytics_modeling_workflow.md`.

## 4. Research-aligned model comparison

V4 separates three baselines that answer different questions.

| Model class | Receives graph-derived values | Receives message passing | Question |
|---|---:|---:|---|
| Raw-feature MLP | No | No | Can endpoint and nomination attributes solve the task? |
| Engineered-graph MLP | Yes | No | Are explicit causal graph statistics sufficient? |
| GNN candidate | Yes | Yes | Does learned neighborhood representation add value beyond explicit graph statistics? |

The previous `causal_mlp_baseline` is renamed
`engineered_graph_mlp_baseline`. Its inputs include reverse-pair counts,
multi-hop path counts, degree-like counts, and temporal neighborhood counts;
calling it a graph-agnostic MLP is inaccurate.

Neither MLP baseline can become the serving GNN model. They are evaluation
controls. The separately governed Tabular engine continues to choose between
Random Forest and `tabular_mlp` and is not changed by this design.

## 5. Existing-corpus contract

The initial v4 evaluation uses the already deployed v4.0 synthetic corpus:

- 401 SQL users, including one non-participating administrator;
- 15,000 nominations;
- 14,700 `LEGITIMATE` dispositions;
- 300 `FRAUD` dispositions;
- six confirmed pattern families with 50 positives each;
- 30 training positives per pattern;
- 10 positives per pattern in each selection period; and
- 10 positives per pattern in the final temporal test.

All 300 fraud labels supervise the overall head. Each pattern head receives
its 50 confirmed pattern labels through a masked multi-label loss. The shared
encoder therefore learns from the complete fraud population rather than from
one 50-example family at a time.

The current corpus is sufficient to implement and compare Model B. Its
per-pattern final-test metrics remain low-volume evidence and must display their
positive counts. A high pattern PR-AUC based on ten final-test positives must
not be presented as production-grade certainty.

No data deletion or reseeding is part of the v4 implementation.

## 6. Supervision

### 6.1 Overall label

The overall head uses the existing normalized disposition:

- `FRAUD` -> positive;
- `LEGITIMATE` -> negative;
- `EXCLUDED`, `NULL`, or otherwise unreviewed -> not a supervised target.

### 6.2 Pattern-label vector

The initial pattern vector is ordered and versioned:

```text
RECIPROCAL
RING
TEMPORAL_BURST
SUPER_NOMINATOR
SUPER_BENEFICIARY
BIPARTITE_DENSE_BLOCK
```

`TrainingDispositionMetadataJson.confirmed_patterns` is the source of the
positive pattern labels. `DecisiveEnginesJson`, Graph Analytics findings, RF
findings, and GNN predictions are prohibited supervision sources.

For the current synthetic corpus, the pattern adjudication is complete:

- a confirmed pattern is `1` for its head;
- an absent pattern on a `FRAUD` row is `0` because the generator declares the
  complete ground-truth pattern set; and
- every pattern is `0` for a `LEGITIMATE` row.

Future human labels may be incomplete. V4 therefore requires a label mask in
the in-memory training contract even though the current corpus supplies a
complete vector. A pattern value whose presence or absence was not adjudicated
is masked out of that head's loss rather than coerced to zero.

Before real-tenant pattern training is enabled, the human-review metadata must
distinguish `COMPLETE`, `PARTIAL`, and `NOT_DETERMINED` pattern adjudication.
That later metadata addition is not required for the synthetic-tenant v4
experiment.

### 6.3 Engine independence

The behavior vocabulary is model-neutral. A label such as `RING` does not mean
that Graph Analytics found a ring. It means an authorized synthetic generator
or independent human adjudication established that behavior.

GNN training and inference must not consume another integrity engine's score,
severity, finding identity, threshold, or routing decision.

## 7. Shared model

### 7.1 Candidate encoder

Each candidate owns exactly one heterogeneous encoder:

- `graphsage`;
- `gcn`; or
- `gatv2`.

Every candidate receives the same graph snapshot, relations, node attributes,
edge attributes, causal ordering, and supervised population. Architecture is
the only intended difference.

The encoder produces one embedding per user. Those embeddings are shared by
the overall head and all pattern heads.

### 7.2 Overall head

The overall head scores:

```text
[nominator embedding | beneficiary embedding | complete causal target vector]
```

It emits one logit and one calibrated fraud probability. This is the only head
that supplies the top-level GNN probability and risk level.

### 7.3 Pattern heads

Each pattern head scores:

```text
[nominator embedding | beneficiary embedding | pattern feature-contract slice]
```

The existing feature contracts remain useful as decoder input contracts:

| Pattern head | Feature contract |
|---|---|
| `RECIPROCAL` | `reciprocal-v1` |
| `RING` | `ring-v1` |
| `TEMPORAL_BURST` | `temporal-burst-v1` |
| `SUPER_NOMINATOR` | `super-nominator-v1` |
| `SUPER_BENEFICIARY` | `super-beneficiary-v1` |
| `BIPARTITE_DENSE_BLOCK` | `bipartite-dense-block-v1` |

The feature slice does not limit what the shared encoder learns from the graph.
It limits only the explicit causal target values supplied to that pattern's
decoder.

Each head emits an independent sigmoid probability. Pattern heads are
multi-label, not mutually exclusive; one nomination may legitimately have
several high pattern probabilities.

### 7.4 Joint objective

The initial training objective is:

```text
total_loss = overall_weight * overall_binary_loss
           + pattern_total_weight * mean(masked_pattern_losses)
```

Each binary loss uses class-balanced binary cross entropy computed from the
applicable training population. The mean is taken only across pattern heads
with supervised targets in the batch or fold. This prevents six pattern heads
from overwhelming the overall objective merely because there are six of them.

Initial policy defaults:

```text
overall_weight       = 1.0
pattern_total_weight = 1.0
```

Focal loss, uncertainty weighting, learned task weighting, and a
mixture-of-experts router are deferred until the shared multi-head baseline is
measured.

## 8. Architecture selection and final testing

### 8.1 Vocabulary

V4 uses conventional terms:

- **training periods** fit model parameters;
- **architecture-validation periods** select GraphSAGE, GCN, or GATv2; and
- **final temporal test** evaluates the frozen selection once.

The final temporal test must never choose an architecture, epoch, threshold,
loss weight, or feature contract. If it is used for any of those choices, it
becomes validation data and a newer untouched test period is required.

### 8.2 Architecture-selection metric

Architecture is selected once for the entire shared model.

The primary selection metric is mean overall-head PR-AUC across the
architecture-validation periods. Pattern-head macro PR-AUC is a documented
tie-breaker when candidates are within the configured overall tie tolerance.
Inference latency is the final tie-breaker.

```text
1. highest mean validation overall PR-AUC
2. highest mean validation macro pattern PR-AUC within overall tie tolerance
3. lowest validation inference latency if still tied
```

No pattern family selects its own encoder architecture.

### 8.3 Final model admission

After architecture selection, the frozen winning candidate is evaluated once
on the final temporal test.

The shared GNN is eligible to serve only when:

1. overall label, temporal, artifact, calibration, and latency gates pass;
2. overall-head PR-AUC exceeds the raw-feature MLP by the configured graph-value
   margin; and
3. overall-head PR-AUC exceeds the engineered-graph MLP by the configured
   message-passing-value margin.

The second comparison answers whether graph information helps. The third
answers whether learned message passing justifies its operational complexity.

The policy may initially set the message-passing margin to zero, but the
comparison must remain explicit and cannot be silently converted into a
descriptive-only result.

After successful final testing, the selected architecture may be refitted on
all matured training and validation labels. The final-test results remain the
recorded evidence for that version; the final test is not repeatedly queried
during refitting.

### 8.4 Pattern-head publication

Pattern heads do not perform architecture selection. Each head receives one of
these states in the selected bundle:

- `ACTIVE`: evidence and calibration gates passed;
- `DIAGNOSTIC_ONLY`: trained and evaluated, but insufficient evidence for live
  pattern claims;
- `INSUFFICIENT_LABELS`: the label gate failed;
- `DISABLED`: policy disabled the head; or
- `FAILED`: deterministic training, artifact, or evaluation failure.

Initial head activation requires:

- minimum training, validation, and final-test positives and negatives;
- final-test PR-AUC above prevalence and the configured margin over that
  head's engineered-graph MLP baseline;
- acceptable temporal variation;
- acceptable calibration; and
- valid artifacts.

A `DIAGNOSTIC_ONLY` head cannot create a live finding or be shown as evidence
to an HRBP reviewer. It remains visible in Model Analysis.

## 9. Evaluation report

Every architecture-validation run records:

- overall PR-AUC, ROC-AUC, Brier score, and lift;
- macro and per-pattern PR-AUC;
- positives and negatives per period;
- precision and recall at the configured HRBP review capacity;
- parameter count, training duration, and inference latency; and
- metric variation across temporal periods.

The final temporal test additionally records:

- comparison with the raw-feature MLP;
- comparison with the engineered-graph MLP;
- per-pattern head state and gate results;
- calibration identity; and
- the frozen selection decision that preceded the test.

Metrics must always show the positive count next to a pattern result. A metric
without its population is incomplete evidence.

## 10. Policy contract

V4 uses `dbo.GNNScoringPolicies.ConfigurationJson` with schema version 4 and a
single forward serving mode:

```json
{
  "schema_version": 4,
  "serving_mode": "shared_encoder_multi_head",
  "model": {
    "hidden_dimension": 64,
    "embedding_dimension": 64
  },
  "training": {
    "epochs": 300,
    "rolling_fold_count": 3,
    "window_days": 365,
    "overall_loss_weight": 1.0,
    "pattern_total_loss_weight": 1.0
  },
  "architecture_selection": {
    "candidate_architectures": ["graphsage", "gcn", "gatv2"],
    "primary_metric": "validation_overall_pr_auc",
    "tie_breaker_metric": "validation_macro_pattern_pr_auc",
    "overall_tie_tolerance": 0.01,
    "minimum_graph_value_over_raw_mlp": 0.02,
    "minimum_message_passing_value_over_engineered_graph_mlp": 0.00
  },
  "pattern_heads": {
    "RECIPROCAL": {
      "enabled": true,
      "feature_contract": "reciprocal-v1"
    },
    "RING": {
      "enabled": true,
      "feature_contract": "ring-v1"
    }
  }
}
```

The abbreviated example does not remove existing artifact retention,
explanation, score-routing, or minimum-population settings. The implementation
must carry them into the schema-version-4 policy rather than using container
environment variables.

Only one active policy row exists per tenant. A policy change takes effect on
the next analytics run and requires no Terraform deployment.

## 11. Artifact contract

One immutable bundle contains all candidates and one serving model:

```text
tenant_<tenant_id>/gnn/<model_version>/
  manifest.json
  graph_snapshot.pt
  candidates/
    raw_feature_mlp/
      metrics.json
    engineered_graph_mlp/
      metrics.json
    graphsage/
      metrics.json
    gcn/
      metrics.json
    gatv2/
      metrics.json
  serving/
    encoder.pt
    decoder.pt
    calibration.json
    metrics.json
```

The serving decoder contains the overall head and every `ACTIVE` pattern head.
Diagnostic candidate artifacts remain immutable but are never loaded by
`integrity-check`.

The `decoder.pt` name preserves the existing versioned blob lookup contract;
its schema-version-4 metadata and active pattern state dictionaries define the
multi-head contents.

The manifest records:

- selected encoder architecture;
- shared model and feature-schema versions;
- ordered pattern taxonomy;
- head feature contracts and states;
- architecture-validation decision;
- final-test evidence;
- baseline comparisons;
- calibration identities;
- graph snapshot identity;
- artifact hashes; and
- explanation compatibility.

`dbo.GNN_UserEmbeddings` stores one embedding generation for the selected
shared encoder, not one generation per pattern head.

## 12. Component diagnostics and run history

`dbo.IntegrityComponentStatus.DiagnosticsJson` is an operational summary, not
an experiment artifact. It must not repeat candidate folds, calibration arrays,
scenario tables, or complete pattern-head evaluations.

The compact diagnostics contain:

- diagnostics schema version;
- run, policy, model, and graph-snapshot identities;
- corpus and temporal-window counts;
- selected architecture;
- compact baseline and final-test summary;
- one compact status per pattern head;
- artifact bundle prefix; and
- serving freshness.

The complete immutable evidence remains in `manifest.json` and candidate
`metrics.json` files. The training-run UI loads the manifest when the user asks
to inspect detailed results.

This boundary prevents UI copy truncation and prevents the temporal component
status history from duplicating large evaluation documents every run.

## 13. Live inference

For one submitted nomination, `integrity-check`:

1. resolves the active GNN bundle from `IntegrityComponentStatus`;
2. loads one multi-head decoder and its manifest;
3. loads version-matched nominator and beneficiary embeddings;
4. constructs the complete causal target vector once;
5. applies the overall and pattern feature slices;
6. executes one decoder forward pass;
7. calibrates the overall and active pattern probabilities;
8. derives the top-level GNN score and risk from the overall probability; and
9. persists active pattern evidence without creating extra engine votes.

The live path must not guess a scenario before scoring. Every active head runs
in the same forward pass.

If the overall head cannot score, GNN is unavailable. Failure of one optional
pattern head suppresses that pattern result but does not replace the overall
probability with zero.

## 14. `GNNResultJson` v4

Illustrative result:

```json
{
  "schema_version": 4,
  "engine": "GNN",
  "available": true,
  "status": "AVAILABLE",
  "model_version": "gnn-v4-20261001-t5-ab12cd34",
  "architecture": "graphsage",
  "model_probability": 0.81,
  "score": 81,
  "risk_level": "HIGH",
  "flagged": true,
  "pattern_heads": {
    "RING": {
      "status": "ACTIVE",
      "probability": 0.92,
      "score": 92,
      "risk_level": "CRITICAL",
      "feature_contract": "ring-v1"
    },
    "RECIPROCAL": {
      "status": "ACTIVE",
      "probability": 0.68,
      "score": 68,
      "risk_level": "HIGH",
      "feature_contract": "reciprocal-v1"
    },
    "SUPER_BENEFICIARY": {
      "status": "DIAGNOSTIC_ONLY",
      "reason": "HEAD_ADMISSION_GATE_NOT_MET"
    }
  },
  "evidence": {
    "material_patterns": ["RING", "RECIPROCAL"],
    "primary_pattern": "RING"
  },
  "explanation": {
    "method": "GNNEXPLAINER",
    "status": "NOT_REQUESTED"
  }
}
```

`model_probability` comes only from the overall head. The fact that the Ring
head is `0.92` does not silently replace the overall `0.81` probability.

## 15. Explanations

GNNExplainer remains asynchronous. A request identifies:

- tenant and nomination;
- shared model version;
- selected architecture;
- graph snapshot;
- target head (`OVERALL` or one active pattern head); and
- idempotency key.

The default explanation target is the overall head plus the highest active
material pattern head. Additional pattern explanations may be requested from
the analysis UI but are not generated automatically for every head.

Explanation output remains inside `GNNResultJson`. No explanation table is
introduced.

## 16. User interface

### 16.1 Engine Status

The GNN status card shows:

- selected shared architecture and model version;
- overall final-test metrics and baseline comparisons;
- pattern-head roster with `ACTIVE`, `DIAGNOSTIC_ONLY`,
  `INSUFFICIENT_LABELS`, `DISABLED`, or `FAILED`;
- population counts beside every pattern metric; and
- a link to the artifact-backed candidate and fold details.

It must not display six separate serving model versions because v4 has one
serving model.

### 16.2 Nomination Analysis

The GNN card shows:

- overall GNN score and risk;
- selected architecture and model version;
- material pattern probabilities;
- causal input table;
- explanation status and explanation when available; and
- explicit diagnostic/unavailable states without rendering them as zero risk.

### 16.3 HRBP review

Only `ACTIVE` pattern-head evidence may be displayed as a proposed pattern.
The reviewer may accept, remove, or add independently supported patterns.
Displaying a proposed pattern never creates a training label until the review
decision is submitted.

## 17. Implementation phases

### V4-D1 — Contracts

- approve this design;
- add schema-version-4 policy and result contracts;
- rename the causal baseline to engineered-graph MLP in code and UI; and
- define compact diagnostics versus full artifact evidence.

### V4-T1 — Shared model

- introduce the shared encoder, overall decoder, and pattern-head module;
- implement masked multi-label loss;
- reuse existing pattern feature contracts as head input slices; and
- add unit tests for loss masking and multi-label behavior.

### V4-T2 — Evaluation

- train all encoder candidates with the identical joint objective;
- implement overall-primary architecture selection;
- implement pattern-aware tie-breaking;
- preserve the final temporal test; and
- compare raw MLP, engineered-graph MLP, and selected GNN explicitly.

### V4-T3 — Artifacts and activation

- publish one shared bundle;
- write one user-embedding generation;
- store compact component diagnostics;
- validate hashes and head contracts; and
- atomically activate the v4 serving pointer.

### V4-I1 — Inference and explanation

- load one multi-head decoder;
- persist `GNNResultJson` schema version 4;
- request head-scoped explanations; and
- retain v2/v3 bundle readers for rollback during the transition.

### V4-U1 — Administrative UI

- replace the v3 specialist roster with a pattern-head roster;
- load detailed candidate evidence from `manifest.json`;
- show population-aware metrics; and
- update Nomination Analysis and HRBP evidence presentation.

### V4-V1 — Synthetic-tenant validation

- run tenant 5 without reseeding;
- compare architecture and baseline results;
- inspect overall and per-pattern test metrics;
- submit live nominations for active heads; and
- decide whether larger or more diverse synthetic corpora are warranted only
  after the shared-head result is available.

## 18. Testing requirements

Automated tests must prove:

- one encoder is shared by the overall and pattern heads;
- every architecture candidate sees identical folds and labels;
- overall and pattern targets are correctly constructed;
- incomplete pattern labels are masked rather than coerced to negatives;
- one nomination may be positive for several pattern heads;
- `DecisiveEnginesJson` and engine findings cannot create labels;
- target nominations never enter their own causal message-passing history;
- architecture selection cannot inspect the final temporal test;
- the final temporal test cannot alter architecture or hyperparameters;
- pattern-head metrics include positive and negative counts;
- a diagnostic-only head cannot produce live HRBP evidence;
- pattern probabilities cannot overwrite the overall GNN probability;
- one pattern-head failure does not zero or invalidate the overall head;
- artifacts and embeddings match the active shared model version;
- component diagnostics remain compact and valid JSON;
- full details remain available through the immutable manifest;
- explanation updates are model-version and head scoped; and
- v2/v3 rollback artifacts remain loadable during the approved retention
  period.

## 19. Deferred work

The following are deliberately outside the first v4 sprint:

- a learned mixture-of-experts router;
- separate encoders by pattern;
- TGN or another event-memory architecture;
- learned fusion of pattern probabilities into the overall probability;
- automatic weak labels from Graph Analytics or another engine;
- real-tenant training on incomplete human pattern adjudications; and
- corpus expansion beyond the existing 15,000 nominations.

The initial code implementation supports overall-head GNNExplainer requests
through the compatible serving decoder. Automatic head-scoped explanation of
the highest material pattern remains pending; the pattern probabilities are
shown as model evidence without claiming a completed pattern explanation.

These items require evidence from the shared multi-head baseline rather than
being introduced preemptively.

## 20. Initial approval recommendations

The recommended initial decisions are:

1. use the existing tenant-5 corpus without reseeding;
2. train GraphSAGE, GCN, and GATv2 as shared-encoder candidates;
3. select one architecture primarily by temporal-validation overall PR-AUC;
4. use macro pattern PR-AUC only as the documented tie-breaker;
5. preserve one untouched final temporal test;
6. require explicit comparison with both raw-feature and engineered-graph MLP
   baselines;
7. route only the overall-head probability;
8. treat pattern heads as multi-label evidence, not additional engine votes;
9. publish weak heads as diagnostic-only rather than pretending that zero means
   no risk; and
10. postpone corpus expansion and mixture-of-experts work until the v4 result
    is measured.
