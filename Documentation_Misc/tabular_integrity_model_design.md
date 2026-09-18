# Tabular Integrity Model Design

**Status:** T5 tenant-first candidate publication and versioned serving cutover implemented
**Owner:** Integrity modeling  
**Applies to:** `fraud-analytics-job`, `integrity-check`, `dbo.IntegrityComponentStatus`, `dbo.IntegrityDecisionResults`, administrative integrity UI  
**Last updated:** 2026-09-17

## 1. Purpose

The current Random Forest model is a tabular integrity model. Its inference is
based on engineered nomination, participant, amount, description-similarity,
and summarized relationship features rather than learned graph message passing.

This design makes the model-family boundary explicit and adds a second eligible
tabular architecture:

```text
Tabular integrity model
├── Random Forest
└── Tabular MLP
```

Random Forest and the Tabular MLP are candidate architectures for one Tabular
integrity engine. They do not create two independent votes from substantially
the same evidence. Both candidates may be trained and evaluated, but only one
selected architecture serves for a tenant and model version. A non-selected
candidate may run in shadow mode for comparison but is non-decisive.

This document defines the model boundary, feature contract, selection contract,
serving identity, explanations, and relationship to GNN.

The behavior-track and graph-architecture contract is defined separately in
`Documentation_Misc/gnn_v3_specialist_serving_design.md`.

The source-adapter, canonical-data, and feature-fitting workflow upstream of
this model family is defined in
`Documentation_Misc/integrity_analytics_modeling_workflow.md`.

## 2. Integrity model-family boundary

The integrity system has two learned-model families with different purposes:

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

Graph Analytics remains a separate deterministic engine. Semantic evaluation
also remains a separate engine. The neutral routing layer receives at most one
Tabular verdict and one GNN verdict, regardless of how many candidates were
evaluated inside either model family.

The name `MLP` alone is insufficient in code, artifacts, logs, manifests, and
the UI. The stable role names are:

- `tabular_mlp`: production-capable candidate in the Tabular engine; and
- `causal_mlp_baseline`: non-serving GNN admission baseline.

Their models, feature contracts, policies, artifacts, metrics, and lifecycle
must remain separate.

## 3. Canonical tabular feature contract

Both tabular candidates receive the same ordered canonical information from the
versioned `award_nomination_tabular_v1` feature builder; they do not query Award
Nomination tables directly:

1. `Amount`
2. `DayOfWeekSin`
3. `DayOfWeekCos`
4. `MonthSin`
5. `MonthCos`
6. `IsWeekend`
7. `NominatorTotalNominations`
8. `NominatorAvgAmount`
9. `NominatorStdAmount`
10. `NominatorUniqueBeneficiaries`
11. `BeneficiaryTotalReceived`
12. `BeneficiaryAvgAmountReceived`
13. `HasReciprocalNomination`
14. `PairNominationCount`
15. `AmountZScore`
16. `IsHighAmount`
17. `NominatorConcentrationRatio`
18. `CategoryFraudRate`
19. `DescriptionCosineSim`
20. `DescriptionEmbDistance`
21. `TransactionalPhraseScore`

This promotes the current RF-v3 information contract to a model-neutral
Tabular-v1 contract while replacing ordinal weekday and month inputs with
sine/cosine pairs. Both coordinates are required: sine alone maps distinct
points on the cycle to the same value. Raw `DayOfWeek` and `Month` remain in the
audit/parity frame but are not model inputs. T2 first proves that canonical
input reproduces the deployed Random Forest calculations. Random Forest and
`tabular_mlp` then consume the same ordered Tabular-v1 matrix; neither candidate
may add private features.

The feature builder must use the same tenant boundary and nomination-time
cutoff for both candidates. No post-decision outcome, future nomination, Graph
Analytics result, GNN result, semantic verdict, composite score, or routing
decision may enter this vector.

The contract is a group of evidence used to produce one combined probability.
It does not create one model or one score per feature.

## 4. Architecture-specific preprocessing

Fair comparison requires the same source information, not necessarily identical
numeric preprocessing. Each candidate may apply a versioned transformation
appropriate to its architecture, provided that the transformation introduces
no additional information.

The Tabular MLP requires a persisted preprocessing pipeline. The initial design
uses:

- binary encoding for `HasReciprocalNomination`;
- bounded numeric handling for similarity and concentration values;
- `log1p` where approved for non-negative count or amount distributions;
- fitted scaling for continuous inputs; and
- the shared Tabular-v1 sine/cosine weekday and month coordinates without a
  candidate-private calendar recoding.

Random Forest may retain tree-appropriate representations. Every manifest must
record canonical feature order, derived input order, transformations, fitted
state, missing-value behavior, and schema version. Training and inference must
load the same persisted preprocessing artifacts.

## 5. Supervision and temporal evaluation

Both candidates must use the same model-neutral labels, provenance rules,
outcome-maturity policy, and temporal train/evaluation boundaries. Candidate
comparison is invalid if one architecture sees different rows, labels, future
information, or evaluation periods.

The initial primary metric should be out-of-time PR-AUC because confirmed fraud
is rare. Selection also evaluates:

- Brier score and calibration;
- precision and recall at the actual HRBP review capacity;
- false-positive rate;
- repeated-fold and repeated-seed stability;
- important cohort and scenario performance;
- inference latency and artifact validity; and
- explanation availability and reproducibility.

The exact selection thresholds belong to a versioned Tabular scoring policy,
not container environment variables.

## 6. Candidate selection and serving

One versioned selection process evaluates `random_forest` and `tabular_mlp`.
The selected architecture is the sole source of the live Tabular verdict.

Initial selection rules are:

1. both candidates train and evaluate against the identical temporal contract;
2. candidates failing label, calibration, artifact, latency, or reproducibility
   guardrails are ineligible;
3. eligible candidates are ranked by the approved primary metric;
4. an effectively tied challenger does not displace the incumbent merely due to
   evaluation noise; and
5. activation atomically moves the Tabular serving pointer only after the
   selected artifact and preprocessing bundle pass validation.

Selection reasons must use stable values such as:

- `HIGHEST_ELIGIBLE_PR_AUC`;
- `INCUMBENT_RETAINED_WITHIN_TOLERANCE`;
- `CHALLENGER_GUARDRAIL_FAILED`;
- `INSUFFICIENT_LABELS`; or
- `NO_ELIGIBLE_TABULAR_CANDIDATE`.

Scoring both models for research is permitted. Fusion must never treat their
correlated outputs as independent engine evidence unless a separately designed
and calibrated ensemble replaces the single-winner contract.

## 7. Inference result contract

The live Tabular result identifies at least:

- selected architecture: `random_forest` or `tabular_mlp`;
- model and feature-schema versions;
- model probability;
- derived score and risk level;
- scoring-policy version;
- availability and stable unavailable reason;
- explanation method and state; and
- structured top feature contributions when available.

The existing `RfResultJson` name is accurate only while Random Forest is the
only serving architecture. Before a Tabular MLP can serve, persistence and API
contracts must adopt a model-neutral Tabular result name or provide an explicit
compatibility transition. An MLP result must never be written or displayed as a
Random Forest result.

The same rule applies to `dbo.IntegrityComponentStatus`: serving identity must
distinguish the Tabular component from its selected architecture.

## 8. Explanations

The explanation UI belongs to the Tabular engine card and must identify the
architecture and explanation method.

- Random Forest uses the approved Tree SHAP implementation.
- Tabular MLP may use a validated SHAP method or Integrated Gradients.

The explanation must attribute the selected model's actual inference, use the
same persisted preprocessing state, pass score-reproduction and stability
checks, and label transformed features in business-readable terms. The UI must
not call an MLP attribution an RF SHAP explanation.

## 9. Artifacts and operational status

Every successful run is stored below the tenant boundary:

```text
ml-models/tenant_<tenant_id>/tabular/<model_version>/
├── manifest.json
├── candidates/
│   ├── random_forest/
│   │   ├── model.pkl
│   │   ├── metrics.json
│   │   └── score_distribution.png
│   └── tabular_mlp/
│       ├── model.pkl
│       ├── metrics.json
│       └── score_distribution.png
└── serving/
    ├── model.pkl
    └── score_distribution.png
```

Candidate artifacts preserve the exact holdout models used for comparison.
The serving artifact is a separate full matured-label refit of the selected
architecture. `manifest.json` is authoritative for the selection and artifact
hashes. The database serving version is changed only after every blob in the
bundle has uploaded successfully.

Each immutable training run records:

- both candidate metrics and eligibility decisions;
- temporal boundaries and label counts;
- selection policy and reason;
- selected and incumbent architectures;
- canonical and architecture-specific feature schemas;
- preprocessing artifacts and hashes;
- candidate model artifacts and hashes; and
- serving-refit and activation metadata.

The Detection Engines UI separates:

1. the currently serving Tabular architecture and version;
2. the latest candidate comparison; and
3. immutable prior training runs.

Only the selected candidate is marked as serving. Shadow scores are visibly
non-decisive and are not shown as additional integrity-engine verdicts.

## 10. Proposed code boundary

The T4 package boundary is:

```text
fraud-analytics-job/
├── feature_builders/tabular/
│   ├── award_nomination_tabular_v1.py
│   └── category_encoding.py
└── modeling/tabular/
    ├── contracts.py
    ├── metrics.py
    ├── preprocessing.py
    ├── splits.py
    ├── training_data.py
    ├── random_forest.py
    ├── tabular_mlp.py
    └── selection.py
```

T4 established the in-memory candidate comparison without changing serving
state. T5 adds immutable candidate and serving bundles, makes
`train_tabular_model.py` the scheduled stage, and lets live inference load the
selected Random Forest or Tabular MLP by registered serving version. The
database component key remains `RF` temporarily for API compatibility; the
manifest and result provenance carry the selected architecture explicitly.
The package must not import GNN's `causal_mlp_baseline`, and GNN must not import
the production `tabular_mlp`.

### 9.1 Deployment and legacy-prefix retirement

The producer and consumers must be deployed as one coordinated cutover because
runtime fallback to old prefixes is deliberately disabled. After deployment:

1. run `fraud-analytics-job` and confirm each available component's registered
   serving version has a complete tenant-first bundle;
2. score a nomination and inspect the Tabular, GNN, and Graph provenance;
3. open the read-only model inspection view and confirm its manifest and
   visualization resolve from that serving version; and
4. only then delete the retired `random_forest/`, `gnn/tenant_*`, and legacy
   Graph prefixes from `ml-models`.

Legacy blobs are rollback material until these checks pass. Removing them is an
explicit post-deployment storage operation, not part of model training.

## 11. Required tests

At minimum, automated tests must prove:

- identical tenant-scoped source rows and labels reach both candidates;
- no target outcome or future nomination leaks into either feature vector;
- preprocessing round-trips exactly between training and inference;
- candidate selection and incumbent retention are deterministic;
- only one tabular architecture can be serving for a tenant/version;
- a shadow result cannot affect routing;
- MLP output is never labeled as Random Forest output;
- explanations identify the selected architecture and reproduce its score; and
- the GNN `causal_mlp_baseline` can never be activated as a Tabular or GNN
  serving model.

## 12. Decisions required before implementation

The following remain implementation-design decisions:

1. Tabular policy persistence and initial selection thresholds;
2. production validation and policy persistence for the initial MLP
   architecture, regularization, and training budget;
3. probability calibration method;
4. model-neutral database and API migration from `RfResultJson`;
5. model-neutral component-status migration from the current RF identity;
6. shadow-scoring duration and retention;
7. explanation method and acceptance thresholds for `tabular_mlp`; and
8. backward-compatible UI rollout.
