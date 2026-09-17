# GNN v3 Scenario-Specialist Serving Design

**Status:** Design draft; no v3 production implementation started  
**Owner:** Integrity modeling  
**Applies to:** `fraud-analytics-job`, `integrity-check`, `integrity-check-extension`, `dbo.GNNScoringPolicies`, `dbo.GNN_UserEmbeddings`, `dbo.IntegrityComponentStatus`, `dbo.IntegrityDecisionResults`, administrative integrity UI  
**Last updated:** 2026-09-17

## 1. Purpose

GNN v2 chooses one graph architecture for one binary GNN score. The tenant's
GraphSAGE, GCN-family, and GATv2 candidates compete against a no-message-passing
MLP baseline, and one eligible graph winner serves the entire GNN engine.

The v2 diagnostics suggest—but do not yet prove—that the best graph
architecture may vary by integrity behavior. Descriptive results from one
synthetic tenant cannot establish a permanent architecture-to-behavior mapping.
Requiring one architecture to be best for every behavior may nevertheless
suppress useful graph signals or cause one well-represented behavior to
dominate selection. V3 is designed to test that hypothesis without hard-coding
its conclusion.

GNN v3 therefore introduces **behavior tracks** in which candidate architectures
compete. Each behavior track:

1. defines one model-neutral integrity behavior, label population, feature
   contract, and evaluation contract;
2. compares all approved graph architectures against the track's causal-feature
   MLP baseline;
3. selects no architecture in advance;
4. admits the empirically supported winner only when its evidence and
   performance gates pass; and
5. otherwise has no serving specialist and explicitly abstains.

Only the admitted winning model becomes that behavior track's **serving
specialist**. `GATv2 Ring specialist`, for example, is a result of temporal
evaluation and admission—not a design-time assignment.

All active specialist results are aggregated into **one GNN engine verdict**.
Specialists are not additional ELCE engines and do not create additional votes
in cross-engine routing.

This document defines the intended v3 model boundary, taxonomy, supervision,
training, admission, artifacts, inference, persistence, explanations, UI,
rollout, and rollback.

## 2. Relationship to GNN v2

GNN v2 remains the implemented production contract until a v3 policy is
explicitly activated. The following documents remain authoritative for v2:

- `Documentation_Misc/gnn_v2_and_integrity_check_extension_design.md`;
- `Documentation_Misc/gnn_v2_training_strategy.md`; and
- `Documentation_Misc/integrity_check_extension_design.md`.

V3 preserves the following v2 guarantees:

- strict tenant isolation;
- causal graph construction and target exclusion;
- explicit model-neutral labels;
- rolling-origin evaluation;
- immutable, versioned artifacts;
- version-matched embeddings and decoders;
- atomic serving activation;
- asynchronous explanations; and
- one GNN result in integrity fusion.

V3 changes the internal GNN serving unit from one tenant-wide binary model to a
set of independently evaluated behavior tracks, each of which may or may not
produce an admitted serving specialist.

The source adapter, canonical integrity data, and feature-fitting stages that
feed both GNN and Tabular modeling are defined in
`Documentation_Misc/integrity_analytics_modeling_workflow.md`. GNN model code
must consume that versioned boundary rather than query an Award Nomination
schema directly after the adapter refactor is complete.

### 2.1 Learned-model family boundary

GNN architecture selection is separate from the Tabular integrity model defined
in `Documentation_Misc/tabular_integrity_model_design.md`:

```text
Tabular integrity model
├── Random Forest          serving candidate
└── Tabular MLP            serving candidate

GNN integrity model
├── GraphSAGE              serving candidate by behavior track
├── GCN                     serving candidate by behavior track
├── GATv2                   serving candidate by behavior track
└── Causal MLP baseline     admission gate only; never serves
```

The two MLPs are different models with different feature contracts, artifacts,
policies, and lifecycles. `tabular_mlp` is eligible to serve as the selected
architecture of the Tabular engine. `causal_mlp_baseline` exists only to test
whether a GNN candidate adds message-passing value and can never score a live
nomination.

The neutral routing layer receives at most one selected Tabular verdict and one
aggregated GNN verdict. Candidate outputs inside either family are not
additional engine votes.

## 3. Non-negotiable engine independence

GNN and Graph Analytics are independent integrity engines. They must remain
opaque to one another during training and inference.

GNN must not consume:

- Graph Analytics findings or pattern identities;
- Graph Analytics scores or severities;
- Graph Analytics thresholds or policy settings;
- Graph Analytics routing decisions; or
- labels inferred from Graph Analytics output.

Graph Analytics must not consume:

- GNN probabilities, specialist scores, or risk levels;
- GNN embeddings;
- GNN explanations; or
- GNN architecture-selection results.

The shared words `RING`, `RECIPROCAL`, or `BIPARTITE_DENSE_BLOCK` describe
model-neutral business behaviors. A behavior label is eligible only when it is
explicit synthetic ground truth or an independent human adjudication. Shared
domain vocabulary does not authorize one engine to supervise another.

The neutral routing layer may receive outputs from GNN, Graph Analytics, the
selected Tabular architecture, and Semantic evaluation. Analytics Reporting may
compare their agreement and disagreement after the fact. Neither activity feeds
an engine's training, admission, or live inference.

## 4. Terminology

### 4.1 Behavior track

A behavior track is the stable evaluation lane for one model-neutral integrity
behavior. It owns the label definition, eligible population, feature contract,
candidate set, temporal folds, and admission policy. It does not name or imply
a preferred graph architecture.

### 4.2 Serving specialist

A serving specialist is the graph model that won a behavior track's architecture
competition and passed final admission. Its selected architecture, model
version, calibration, thresholds, availability, and explanation state are
independent from every other serving specialist. A track with no admitted model
has no serving specialist.

### 4.3 Feature group

A feature group is a versioned set of causal inputs made available to every
candidate in a behavior track under the applicable fairness contract. Feature
groups are inputs; Ring, Reciprocal, and Burst are outcomes. The terms must not
be used interchangeably.

### 4.4 Causal-feature MLP

The causal-feature MLP receives the behavior track's permitted
non-message-passing inputs but no learned graph embeddings. It is the track's
admission baseline and can never become a serving GNN specialist. Its stable
role name is `causal_mlp_baseline`; it is not the production-capable
`tabular_mlp` candidate defined for the Tabular integrity engine.

### 4.5 Abstention

An unadmitted or unavailable specialist returns no risk evidence. Abstention is
not a zero score and must not be persisted or displayed as `NONE` risk.

### 4.6 Mixed evidence

`MIXED` is a derived result produced when multiple admitted specialists emit
material evidence for the same nomination. It is not trained as a separate
specialist.

## 5. Initial behavior-track taxonomy

The initial v3 taxonomy is:

| Behavior-track key | Behavior | Prediction unit | Initial status |
|---|---|---|---|
| `RECIPROCAL` | Suspicious two-way nomination exchange | Candidate edge and directed pair | Planned |
| `RING` | Candidate closes or strengthens a directed cycle | Candidate edge plus causal path | Planned |
| `TEMPORAL_BURST` | Coordinated activity forms in a short time window | Candidate edge and local temporal neighborhood | Planned |
| `SUPER_NOMINATOR` | Nominator exhibits independently substantiated abnormal outgoing behavior | Nominator node associated with candidate | Planned |
| `SUPER_BENEFICIARY` | Beneficiary exhibits independently substantiated abnormal incoming behavior | Beneficiary node associated with candidate | Planned |
| `BIPARTITE_DENSE_BLOCK` | Candidate participates in a suspicious dense many-to-many campaign | Candidate edge and induced subgraph | Planned |

### 5.1 Explicit exclusions

The following are not initial GNN behavior tracks:

- `AMOUNT`: primarily an RF or rules-based feature, not intrinsically a graph
  topology behavior;
- `MIXED`: derived from multiple specialist signals;
- `NOMINATION_DESERT`: an organizational participation signal, not evidence
  that a submitted nomination is fraudulent;
- `HIDDEN_CANDIDATE`: an organizational analytics signal unless a later,
  independently labelled integrity use case is approved; and
- semantic category alignment or description quality: owned by the Semantic
  engine.

Nomination Desert remains analytics-only. A team with no nomination edges has
no candidate event for per-nomination inference, and absence of participation
must not route a nomination to HRBP.

### 5.2 Concentration terminology

The generic synthetic `CONCENTRATION` label is too broad for specialist
serving. Future corpora must identify the actual independently adjudicated
behavior, such as:

- repeated directed-pair concentration;
- super-nominator behavior;
- super-beneficiary behavior; or
- bipartite dense-block participation.

Existing broad concentration labels may remain available for v2 evaluation but
are not sufficient to admit a v3 specialist.

## 6. Supervision contract

### 6.1 Model-neutral dispositions

The nomination-level training disposition remains:

- `FRAUD`;
- `LEGITIMATE`;
- `EXCLUDED`; or
- `NULL`.

V3 adds an optional multi-label pattern adjudication inside
`TrainingDispositionMetadataJson`:

```json
{
  "schema_version": 3,
  "pattern_taxonomy_version": "gnn-v3-patterns-v1",
  "confirmed_patterns": ["RECIPROCAL", "TEMPORAL_BURST"]
}
```

Pattern provenance and review time remain in the existing typed columns:

- `TrainingDispositionSource` identifies the workflow that established the
  label, such as `HUMAN_INVESTIGATION`, `RANDOM_AUDIT`, or
  `SYNTHETIC_GROUND_TRUTH`;
- `ReviewedBy` identifies the reviewer or authorized seeding process; and
- `ReviewedAt` records when the adjudication occurred.

The metadata JSON must not duplicate those values because parallel copies can
diverge. The application, not the reviewer, sets `TrainingDispositionSource`
from the workflow being completed. `DecisiveEnginesJson` must never populate
either label provenance or `confirmed_patterns`: it records engine and routing
evidence, not independent ground truth. Copying it would feed model output back
into model supervision and would violate the independence of GNN and Graph
Analytics.

Synthetic ground truth carries the same normalized `confirmed_patterns` array.
The existing `scenario_family` value may be translated only by the corpus
loader under an explicit, versioned mapping.

### 6.2 Positive and negative populations

For specialist `S`:

- a `FRAUD` disposition containing `S` is positive;
- a `LEGITIMATE` disposition is negative;
- a `FRAUD` disposition confirmed only for other patterns is excluded from
  specialist `S`, not treated as negative;
- `EXCLUDED` and unreviewed nominations are not supervised targets; and
- nominations without pattern adjudication may remain graph history but cannot
  supervise a specialist.

This prevents one valid fraud behavior from becoming a false negative for
another specialist.

### 6.3 Human adjudication requirement

Real-tenant specialist labels require an independent human workflow capable of
confirming zero, one, or several patterns. The reviewer is not required to
classify an already identified specialist finding a second time. The review UI
displays the proposed patterns from the structured specialist findings in
`GNNResultJson`, together with their evidence, and treats the displayed
proposals as part of the confirmation action.

`DecisiveEnginesJson` is not the source of the pattern identity. It identifies
which engines affected the composite decision or route; it does not contain the
specialist finding contract. Merely displaying or routing on an engine finding
must not create a training label.

The `Confirmed Fraud` action must state which displayed patterns will also be
confirmed. By default, the reviewer can accept the proposed patterns without
selecting them again. When several patterns are proposed, the reviewer can
remove an unsupported pattern or add a different supported pattern before
submitting. The submitted review, rather than the original engine output,
creates the adjudicated pattern-label snapshot.

When a reviewer selects `Confirmed Fraud`, the system persists:

- `TrainingDisposition = 'FRAUD'`;
- `TrainingDispositionSource = 'HUMAN_INVESTIGATION'` for the investigation
  workflow;
- the authenticated reviewer and review timestamp in `ReviewedBy` and
  `ReviewedAt`; and
- zero, one, or several explicitly accepted `confirmed_patterns` in
  `TrainingDispositionMetadataJson`.

`Pattern not determined` is a valid outcome. It produces a valid general
`FRAUD` disposition but is not eligible as a positive label for any v3 behavior
track. The UI must not force a reviewer to guess a pattern merely to complete
the review.

### 6.4 Synthetic scenario requirements

Every synthetic specialist scenario requires difficult legitimate decoys:

- Super Nominator: legitimate coordinators and campaign champions with high
  outgoing volume;
- Super Beneficiary: legitimately popular recipients with broad support;
- Bipartite Dense Block: legitimate project or cross-functional groups with
  dense recognition;
- Temporal Burst: legitimate event-driven nomination surges;
- Reciprocal: ordinary mutual recognition separated by realistic time and
  context; and
- Ring: open chains and concentrated relationships that do not close a cycle.

High activity alone must never determine the label.

## 7. Feature contracts

Each behavior track owns a versioned feature contract containing:

- eligible causal-context features;
- permitted node attributes;
- permitted edge and relation types;
- temporal windows;
- missing-value behavior;
- target entity and affected roles; and
- feature schema version.

The baseline and graph candidates for one behavior track receive the same permitted
non-topological information. Only the graph candidates receive learned message-
passing representations.

No behavior track may consume a Boolean or score produced by another integrity
engine. In particular, `UserGraphFlags`, Graph pattern findings, RF scores, and
semantic findings are prohibited.

## 8. Candidate architectures

Every configured behavior track evaluates:

1. its causal-feature MLP admission baseline;
2. heterogeneous GraphSAGE;
3. heterogeneous GCN-family candidate implemented with `GraphConv`; and
4. heterogeneous GATv2.

TGN may be added as a future candidate after its temporal-memory lifecycle,
artifact contract, and live-inference requirements are implemented.

Architectures compete only within the same behavior track. A Ring candidate
does not compete with a Reciprocal candidate, and one track's winner cannot
admit another track. There is no static mapping such as `RING = GATV2` or
`TEMPORAL_BURST = GRAPHSAGE`.

The winning architecture is unknown before evaluation. It is selected for one
tenant, behavior track, training window, feature-contract version, and policy
version. A different tenant or later time window may produce a different
winner. Historical winners are incumbents, not prior truths. When the evidence
cannot distinguish an eligible winner, the track retains a valid incumbent
under the documented tolerance or abstains when none exists.

## 9. Temporal evaluation and selection

### 9.1 Nested temporal responsibilities

Architecture choice and final admission must not use the same evidence.

For each behavior track:

1. earlier rolling-origin folds compare eligible architectures;
2. the architecture with the strongest stable selection-fold result becomes
   the provisional winner;
3. that architecture is fitted without the newest holdout;
4. the newest untouched holdout verifies admission; and
5. only after admission may the winner be refitted on all matured eligible
   labels.

Scenario metrics pooled across all folds are descriptive only. They cannot both
choose and validate a behavior track's architecture.

### 9.2 Minimum evidence gates

Every behavior-track policy must declare at least:

- minimum positive and negative training labels;
- minimum positive and negative final-holdout labels;
- minimum number of evaluable temporal folds;
- maximum allowed fold instability;
- minimum PR-AUC improvement over its causal-feature MLP;
- precision or recall requirement at the available review capacity;
- calibration/Brier guardrail;
- latency and artifact guardrails; and
- incumbent refresh tolerance.

The initial numeric values remain a policy decision. A specialist with only a
few final-holdout positives must return `INSUFFICIENT_SPECIALIST_LABELS`, even
when its descriptive PR-AUC appears high.

### 9.3 Admission rules

A behavior track's provisional winning architecture is admitted only when it:

1. passes all label and artifact gates;
2. beats its own causal-feature MLP by the configured margin on the final
   untouched holdout;
3. satisfies stability, calibration, review-capacity, and latency guardrails;
   and
4. has a reproducible, version-matched artifact bundle.

Graph Analytics performance is not an admission baseline and does not enter
this process.

### 9.4 Incumbent refresh and switching

Admission, refresh, and switching are distinct decisions:

- **Initial admission:** requires the full improvement margin over the
  specialist MLP.
- **Incumbent refresh:** an eligible incumbent may be retrained and republished
  when it remains within the configured tolerance of its MLP, even if it does
  not clear the initial admission margin again.
- **Architecture switch:** a challenger must pass initial admission and beat
  the incumbent by more than the switch tolerance.

This prevents stale specialist embeddings without allowing weak challengers to
churn the serving architecture.

### 9.5 Stable result codes

The specialist evaluator uses explicit reason codes, including:

- `ADMITTED_HIGHEST_ELIGIBLE_PR_AUC`;
- `INCUMBENT_REFRESHED_WITHIN_TOLERANCE`;
- `INCUMBENT_RETAINED_CHALLENGER_TIED`;
- `NO_MESSAGE_PASSING_VALUE_OVER_MLP`;
- `INSUFFICIENT_SPECIALIST_LABELS`;
- `INSUFFICIENT_TEMPORAL_COVERAGE`;
- `SPECIALIST_GUARDRAIL_FAILED`;
- `SPECIALIST_DISABLED`; and
- `NO_ACTIVE_SPECIALIST_MODEL`.

## 10. Training orchestration

The weekly tenant run performs the following:

1. load the active `dbo.GNNScoringPolicies` row;
2. build one tenant-isolated causal graph population;
3. load model-neutral nomination and pattern labels;
4. construct each configured behavior-track population;
5. evaluate each track's baseline and graph candidates under identical folds;
6. independently admit, refresh, retain, or abstain each behavior track;
7. refit newly admitted or refreshed specialists;
8. write embeddings and immutable specialist artifacts;
9. build one bundle manifest referencing all active specialist versions;
10. validate every referenced artifact and embedding generation; and
11. atomically move the tenant's GNN serving pointer to the new bundle.

A failure in one specialist must not erase healthy specialists. The bundle may
carry forward a still-valid incumbent specialist version while activating a new
version for another specialist.

## 11. Policy configuration

V3 settings remain in `dbo.GNNScoringPolicies.ConfigurationJson`. No Terraform
deployment is required to adjust model policy.

Illustrative shape:

```json
{
  "schema_version": 3,
  "serving_mode": "scenario_specialists_v3",
  "behavior_tracks": {
    "RECIPROCAL": {
      "enabled": true,
      "candidate_architectures": ["graphsage", "gcn", "gatv2"],
      "feature_contract": "reciprocal-v1",
      "minimum_train_positives": 30,
      "minimum_holdout_positives": 15,
      "minimum_improvement_over_mlp": 0.02,
      "incumbent_refresh_tolerance": 0.01
    },
    "RING": {
      "enabled": true,
      "candidate_architectures": ["graphsage", "gcn", "gatv2"],
      "feature_contract": "ring-v1",
      "minimum_train_positives": 30,
      "minimum_holdout_positives": 15,
      "minimum_improvement_over_mlp": 0.02,
      "incumbent_refresh_tolerance": 0.01
    }
  },
  "aggregation": {
    "method": "maximum_calibrated_probability",
    "mixed_minimum_specialists": 2
  }
}
```

The numeric values above are examples, not approved production defaults.

## 12. Artifact and embedding contract

### 12.1 Bundle structure

One immutable tenant bundle records all specialist decisions:

```text
gnn/tenant_<tenant_id>/<bundle_version>/
  manifest.json
  graph_snapshot.pt
  specialists/
    reciprocal/
      candidates/
        mlp_causal/metrics.json
        graphsage/metrics.json
        gcn/metrics.json
        gatv2/metrics.json
      serving/
        encoder.pt
        decoder.pt
        calibration.json
        metrics.json
    temporal_burst/
      ...
```

The bundle manifest maps every configured behavior track to its serving
specialist state:

- `ACTIVE`, `CARRIED_FORWARD`, `NOT_ADMITTED`, `DISABLED`, or `FAILED`;
- selected architecture;
- specialist model version;
- feature contract version;
- graph snapshot identity;
- calibration identity;
- admission evidence;
- thresholds;
- artifact hashes; and
- explanation compatibility.

### 12.2 Reusing the embedding table

`dbo.GNN_UserEmbeddings` is already keyed by tenant, user, model version, and
date. V3 can store several specialist embedding generations without adding a
new embedding table. Every specialist model version must be unique and remain
within the existing `VARCHAR(64)` limit.

`dbo.IntegrityComponentStatus.ServingVersion` points to the bundle version, not
an individual specialist. The manifest resolves the bundle to each specialist's
model and embedding version.

Retention must preserve every model version referenced by the active bundle or
its rollback predecessors.

## 13. Live inference

For a submitted nomination, `integrity-check`:

1. loads the active GNN v3 bundle manifest;
2. enumerates all active specialists;
3. loads each specialist decoder and its version-matched endpoint embeddings;
4. constructs the specialist's causal feature vector;
5. scores every active specialist independently;
6. records unavailable or abstaining specialists explicitly;
7. calibrates each available specialist probability;
8. assigns specialist risk using its recorded thresholds; and
9. aggregates the specialist evidence into one GNN result.

Inference must not first guess a scenario and then run only that specialist.
The true scenario is unknown at submission time. Every active specialist runs.

Failure of one specialist does not produce a zero score and does not invalidate
another specialist. GNN is unavailable only when no specialist can produce a
valid result.

## 14. GNN result JSON v3

Illustrative `GnnResultJson`:

```json
{
  "schema_version": 3,
  "engine": "GNN",
  "available": true,
  "status": "AVAILABLE",
  "model_version": "gnn-v3-20261001-t5-ab12cd34",
  "model_probability": 0.81,
  "score": 81,
  "risk_level": "HIGH",
  "flagged": true,
  "bundle_version": "gnn-v3-20261001-t5-ab12cd34",
  "specialists": {
    "RECIPROCAL": {
      "available": true,
      "architecture": "gcn",
      "model_version": "g3-t5-recip-gcn-ab12cd34",
      "probability": 0.81,
      "score": 81,
      "risk_level": "HIGH",
      "finding": "Reciprocal specialist detected elevated pair behavior"
    },
    "RING": {
      "available": false,
      "status": "NOT_ADMITTED",
      "reason": "NO_MESSAGE_PASSING_VALUE_OVER_MLP"
    },
    "TEMPORAL_BURST": {
      "available": true,
      "architecture": "graphsage",
      "model_version": "g3-t5-burst-sage-34ef5678",
      "probability": 0.67,
      "score": 67,
      "risk_level": "HIGH"
    }
  },
  "aggregate": {
    "method": "maximum_calibrated_probability",
    "probability": 0.81,
    "score": 81,
    "risk_level": "HIGH",
    "decisive_specialists": ["RECIPROCAL"],
    "mixed_evidence": true
  }
}
```

The existing top-level `model_version`, `model_probability`, `score`,
`risk_level`, and `flagged` fields are preserved for backward-compatible
integrity fusion and are derived from `aggregate`. The specialist document is
additive. Cross-engine fusion still receives one GNN engine verdict.

## 15. Specialist aggregation

The initial aggregation method is the maximum calibrated specialist
probability. It is intentionally conservative and avoids treating correlated
specialist probabilities as independent.

`MIXED` evidence is informational and is set when at least the configured
number of specialists cross their material-evidence thresholds. It does not
apply a hidden score bonus in v3 unless a later policy explicitly defines and
validates one.

Noisy-OR, stacking, or another learned fusion method is deferred. Such a method
would require independent labels and a separate temporal evaluation contract.

## 16. Explanations

GNN explanations remain asynchronous. An explanation request must include:

- tenant and nomination identity;
- bundle version;
- specialist key;
- specialist model version;
- graph snapshot identity; and
- idempotency key.

By default, `integrity-check` requests GNNExplainer only for the decisive
specialist or specialists. It does not request explanations for abstaining or
low-signal specialists.

The extension worker writes explanation state under the corresponding
specialist in `GnnResultJson`. One specialist's explanation failure must not
erase another specialist's explanation or inference result.

## 17. Component status and training-run UI

The existing single `GNN` row in `dbo.IntegrityComponentStatus` remains the
component-level pointer. Its diagnostics contain:

- bundle status and version;
- active, carried-forward, unadmitted, disabled, and failed specialist counts;
- one status record per specialist;
- candidate comparison per specialist;
- label and temporal-fold diagnostics;
- artifact prefix; and
- last successful specialist refresh times.

The administrative UI should show:

1. the active GNN bundle;
2. a specialist roster with serving architecture and version;
3. per-specialist candidate comparison;
4. per-fold and per-scenario evaluation;
5. abstention and failure reasons;
6. incumbent age and embedding freshness; and
7. immutable artifacts.

The Nomination Analysis GNN card should show one row per specialist and clearly
distinguish `NONE` risk from `NOT_ADMITTED`, `UNAVAILABLE`, and `DISABLED`.

## 18. Observability

Nomination-scoped logs include:

- `GNN specialist assessment starting`;
- `GNN specialist assessment completed`;
- specialist key, architecture, model version, score, and risk;
- explicit abstention or unavailability reason; and
- final GNN specialist aggregation.

Training logs include one start/completion/failure record per specialist and
candidate. Metrics must support filtering by tenant, bundle version,
specialist, architecture, fold, and result code.

## 19. Rollout

### Phase V3-D1 — Documentation and contracts

- approve taxonomy and independence rules;
- approve behavior-track label metadata;
- approve per-track selection and serving-specialist manifest contracts; and
- approve admission, refresh, and abstention semantics.

### Phase V3-D2 — Diagnostic evaluator

- emit per-fold scenario metrics rather than pooled-only scenario metrics;
- enforce sample-size gates;
- select provisionally on earlier folds and verify on the newest holdout;
- produce recommendations without changing serving; and
- expand the synthetic corpus with specialist scenarios and legitimate decoys.

### Phase V3-T1 — Training and artifacts

- train per-behavior-track candidates;
- write specialist artifacts and calibrators;
- support independent incumbent retention and refresh;
- publish a v3 bundle manifest; and
- retain v2 serving activation.

### Phase V3-I1 — Shadow inference

- run every admitted v3 specialist without routing impact;
- persist v3 shadow results separately in diagnostics;
- verify latency, artifact loading, embedding versioning, and score stability;
- compare predictions only with model-neutral outcomes; and
- confirm no cross-engine data dependency.

### Phase V3-S1 — Tenant-scoped activation

- activate `scenario_specialists_v3` for the synthetic validation tenant;
- keep v2 rollback artifacts and embeddings;
- validate live nomination results and explanations; and
- expand to additional tenants only after explicit approval.

## 20. Rollback

Rollback changes the component serving pointer to the prior v2 or v3 bundle.
The referenced decoders, manifest, graph snapshot, calibrators, and embedding
versions must still exist.

V3 rollout must not delete v2 artifacts or rewrite v2 manifests. A tenant can
return to `single_winner_v2` without retraining if its retained v2 artifacts and
embeddings remain valid.

## 21. Testing requirements

At minimum, automated tests must prove:

- tenant isolation for every specialist;
- target nominations never enter their own message-passing graph;
- Graph Analytics and other engine outputs cannot enter specialist features or
  labels;
- `causal_mlp_baseline` cannot be activated or persisted as a live GNN or
  Tabular scorer;
- `DecisiveEnginesJson` cannot populate `confirmed_patterns` or training-label
  provenance;
- the client cannot choose `TrainingDispositionSource`; it is derived from the
  authorized review or seeding workflow;
- multi-label positives and cross-specialist exclusions are correct;
- provisional architecture selection never reads the final holdout;
- admission sample gates cannot be bypassed by high PR-AUC on tiny populations;
- one specialist failure does not zero another specialist;
- abstention is distinct from `NONE` risk;
- bundle activation is atomic;
- embeddings match the specialist decoder model version;
- carried-forward incumbents remain scoreable;
- mixed evidence does not create an undocumented score bonus;
- explanation updates are specialist-scoped and idempotent; and
- v2 rollback remains functional.

## 22. Decisions to finalize before implementation

The following require explicit approval before v3 serving implementation:

1. final behavior-track taxonomy and normalized keys;
2. human pattern-adjudication UI and permissions;
3. minimum train and final-holdout counts by specialist;
4. fold-stability and confidence requirements;
5. calibration method and per-specialist risk thresholds;
6. exact incumbent refresh and architecture-switch tolerances;
7. artifact/model-version naming within the 64-character database limit;
8. v3 shadow-result persistence during rollout; and
9. production eligibility criteria after synthetic-tenant validation.
