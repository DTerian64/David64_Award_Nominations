# Synthetics Inc. v4 Specialist Corpus Replacement

**Status:** Implemented and deployed; retained as the v4 corpus baseline
**Owner:** Integrity modeling  
**Applies to:** Synthetics Inc., `scripts/synthetic_tenant`, `fraud-analytics-job`, GNN v3 scenario specialists  
**Supersedes:** The deployed Synthetics Inc. v3 nomination corpus, but not the tenant, users, configuration, or policies  
**Last updated:** 2026-09-21

Proposed participation-analytics changes belong to the separate
[v5 design](synthetics_inc_v5_participation_analytics_design.md); they do not
retroactively change the deployed v4 corpus or its manifest.

## 1. Purpose

The deployed v3 corpus was designed before the GNN behavior tracks were split
into independently admitted specialists. It contains general fraud dispositions
and several broad scenario families, but it does not provide explicit positive
labels for every specialist. In particular, Graph Analytics can detect
`BipartiteDenseBlock` findings while the GNN `BIPARTITE_DENSE_BLOCK` specialist
has zero eligible positive labels.

V4 replaces the nomination corpus with deterministic synthetic ground truth
that covers every initial GNN behavior track. It keeps the current tenant,
directory identities, SQL users, roles, categories, application configuration,
and scoring policies.

This document does not authorize or perform the deletion. Reset and reseeding
remain manual operations and occur only after the rollback preview has been
reviewed.

## 2. Fixed corpus identity

| Property | V4 value |
|---|---|
| Tenant | `Synthetics Inc.` |
| Organization ID | `f74bff31-f42f-4461-a1dd-e1ae978c1abe` |
| SQL tenant | Resolve by organization ID; never hard-code `TenantId` |
| Corpus users | 400 synthetic users |
| Operational administrator | 1; excluded from corpus activity |
| Nominations | 15,000 |
| Legitimate outcomes | 14,700 |
| Fraud outcomes | 300 |
| Fraud prevalence | 2% |
| History window | 365 days |
| Temporal segments | 5 equal chronological segments, `S0` through `S4` |
| Generator version | `synthetics-inc-v4.0` |
| Proposed deterministic seed | `20260921` |
| Stable directory seed | `20260912` (preserves the deployed 400-user roster) |
| Inclusive window start | `2025-09-22` |
| Inclusive window end | `2026-09-21` |
| Exclusive generator as-of boundary | `2026-09-22T00:00:00Z` |
| Pattern taxonomy | `gnn-v3-patterns-v1` |

Changing the seed, as-of boundary, taxonomy, distribution, or topology recipes
creates a different corpus identity and therefore a different corpus SHA-256.

## 3. Non-negotiable boundaries

1. `confirmed_patterns` is synthetic or human ground truth. It is never copied
   from Graph Analytics, GNN inference, RF, Tabular MLP, semantic findings,
   `DecisiveEnginesJson`, or routing output.
2. GNN and Graph Analytics remain independent. Agreement and disagreement may
   be measured after inference, but one engine never labels the other.
3. V4 performs no scenario compatibility mapping. For a fraudulent target,
   `scenario_family` is exactly one behavior-track key and
   `confirmed_patterns` contains exactly that same key. The loader reads the
   array directly; it never derives, translates, aliases, or backfills it from
   `scenario_family`.
4. A legitimate nomination has `confirmed_patterns: []` and is a negative for
   every specialist, including when it is a deliberately difficult decoy.
5. Every v4 fraudulent target contains exactly one confirmed GNN pattern.
   Multi-pattern and non-specialist fraud are deliberately excluded from this
   bootstrap corpus.
6. Fraud confirmed for one specialist is excluded from every other specialist.
7. Every target is scored from strictly prior topology. Target nominations
   cannot create their own graph, node, or causal-context evidence.

## 4. Canonical scenario taxonomy

### 4.1 GNN specialist families

These six values are the only initial GNN pattern labels:

| Scenario family | `confirmed_patterns` value | Specialist behavior |
|---|---|---|
| `RING` | `["RING"]` | Candidate closes or materially strengthens a three- or four-user directed cycle |
| `RECIPROCAL` | `["RECIPROCAL"]` | Candidate completes a suspicious reverse-direction exchange |
| `TEMPORAL_BURST` | `["TEMPORAL_BURST"]` | Candidate completes coordinated activity in a short time window |
| `SUPER_NOMINATOR` | `["SUPER_NOMINATOR"]` | Candidate nominator has independently suspicious outgoing breadth or volume |
| `SUPER_BENEFICIARY` | `["SUPER_BENEFICIARY"]` | Candidate beneficiary has independently suspicious incoming breadth or volume |
| `BIPARTITE_DENSE_BLOCK` | `["BIPARTITE_DENSE_BLOCK"]` | Candidate completes or materially strengthens a suspicious dense many-to-many block |

### 4.2 Legitimate family

Every legitimate row uses `scenario_family = "LEGITIMATE"` and
`confirmed_patterns = []`. A `scenario_variant` may distinguish ordinary
background activity from a hard negative, but it never maps to a specialist
label.

`AMOUNT`, `MIXED`, `NOMINATION_DESERT`, and `HIDDEN_CANDIDATE` are deliberately
absent from the v4 specialist-bootstrap taxonomy. The Tabular and analytics
engines may still evaluate the same nominations from their own features, but
this corpus does not create separate synthetic truth families for them.
Multi-label behavior is deferred until all six single-pattern specialists have
an admitted architecture.

## 5. Exact fraud allocation

Each temporal segment contains exactly 3,000 nominations and 60 fraud targets.
The same allocation is repeated in every segment so that model eligibility does
not depend on a lucky temporal split.

| Scenario family | Per segment | Five-segment total |
|---|---:|---:|
| `RING` | 10 | 50 |
| `RECIPROCAL` | 10 | 50 |
| `TEMPORAL_BURST` | 10 | 50 |
| `SUPER_NOMINATOR` | 10 | 50 |
| `SUPER_BENEFICIARY` | 10 | 50 |
| `BIPARTITE_DENSE_BLOCK` | 10 | 50 |
| **Total** | **60** | **300** |

Each family therefore has the same prevalence, the same temporal coverage, and
the same opportunity to select a graph architecture. No family is synthesized
from another family and no compatibility map participates in label creation.

## 6. Expected specialist-label coverage

The current three-fold rolling-origin implementation divides the history into
five chronological segments:

| Segment | Rolling-origin use | Positives per specialist |
|---|---|---:|
| `S0` | Initial message-passing history | 10 |
| `S1` | Fold 1 training | 10 |
| `S2` | Fold 1 evaluation; Fold 2 training | 10 |
| `S3` | Fold 2 evaluation; Fold 3 training | 10 |
| `S4` | Final untouched holdout | 10 |

Expected admission evidence for every specialist is therefore:

| Gate | Expected | Current requirement |
|---|---:|---:|
| Rolling training positives (`S1` + `S2` + `S3`) | 30 | 15 |
| Final holdout positives (`S4`) | 10 | 5 |
| Evaluable selection folds | 2 | 2 |
| Training legitimate labels | Well above 100 | 100 |
| Final holdout legitimate labels | Well above 100 | 100 |

The margin above the minimum protects the run from small future changes in
eligibility filtering. Passing label gates does not guarantee model admission;
each specialist must still beat its causal MLP baseline and pass stability,
calibration, and latency guardrails.

## 7. Metadata contract

Every v4 `dbo.IntegrityDecisionResults` row uses
`TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH'` and carries valid JSON in
`TrainingDispositionMetadataJson`.

Example single-pattern target:

```json
{
  "schema_version": 3,
  "generator_version": "synthetics-inc-v4.0",
  "pattern_taxonomy_version": "gnn-v3-patterns-v1",
  "generation_run_id": "<uuid>",
  "corpus_sha256": "<sha256>",
  "seed": 20260921,
  "directory_seed": 20260912,
  "logical_nomination_id": "<stable logical id>",
  "stable_nomination_id": "<stable id>",
  "temporal_segment": 4,
  "scenario_id": "<stable scenario id>",
  "scenario_family": "BIPARTITE_DENSE_BLOCK",
  "scenario_variant": "ACTIVE_3X3_COMPLETION",
  "scenario_phase": "TARGET",
  "context_mode": "ACTIVE",
  "ground_truth": "FRAUD",
  "confirmed_patterns": ["BIPARTITE_DENSE_BLOCK"]
}
```

Example legitimate hard negative:

```json
{
  "schema_version": 3,
  "pattern_taxonomy_version": "gnn-v3-patterns-v1",
  "scenario_family": "LEGITIMATE",
  "scenario_variant": "CROSS_FUNCTIONAL_RECOGNITION_CAMPAIGN",
  "scenario_phase": "TARGET",
  "context_mode": "ACTIVE",
  "ground_truth": "LEGITIMATE",
  "confirmed_patterns": []
}
```

Rules:

- `confirmed_patterns` is always present, including as an empty array;
- a fraud target contains exactly one value, identical to `scenario_family`;
- a legitimate row contains no values and uses `scenario_family = LEGITIMATE`;
- precursor and background rows are `LEGITIMATE` with an empty array;
- v4 label loading reads `confirmed_patterns` directly and does not invoke the
  legacy `scenario_family` compatibility mapping; and
- an absent or malformed array is a corpus validation error, never a reason to
  fall back to another metadata field.

## 8. Causal topology recipes

Each target has sufficient strictly prior evidence for its claimed behavior.
The exact counts are generator-contract values, not Graph Analytics policy
thresholds.

### 8.1 Ring

- Split examples between three-user and four-user cycles.
- A three-user target closes `A -> B -> C -> A`.
- A four-user target closes `A -> B -> C -> D -> A`.
- Precursor edges precede the target and the target edge is absent from history.
- Open-chain and non-closing-cycle variants are legitimate hard negatives.

### 8.2 Reciprocal

- History contains `A -> B`; the target is `B -> A`.
- Additional context prevents the identity of the target from being determined
  solely by one duplicated pair.
- Ordinary mutual recognition separated by time, category, and context is a
  legitimate hard negative.

### 8.3 Temporal burst

- Three to five prior events involving the target endpoints occur within the
  configured one-hour causal window.
- The target completes the suspicious cluster.
- Legitimate event-driven surges, team launches, and award deadlines are hard
  negatives with comparable volume but broader organizational context.

### 8.4 Super nominator

- The target nominator has prior 30-day outgoing volume and beneficiary breadth
  substantially above the background distribution.
- History uses several distinct beneficiaries rather than a single repeated
  pair shortcut.
- Legitimate coordinators, managers, and culture champions provide matched
  high-volume hard negatives.

### 8.5 Super beneficiary

- The target beneficiary has prior 30-day incoming volume and nominator breadth
  substantially above the background distribution.
- History uses several distinct nominators.
- Legitimately popular recipients, project leads, and milestone recipients
  provide matched hard negatives.

### 8.6 Bipartite dense block

- Use a minimum 3-by-3 nominator/beneficiary group.
- Prior history contains at least eight of the nine directed group edges; the
  target completes the block or materially raises its density.
- Actor pools are scenario-controlled so the shape is not accidentally created
  by unrelated background rows.
- Legitimate cross-functional recognition campaigns create equally dense
  blocks with valid business context and remain negative labels.

## 9. Context-mode allocation

Every specialist must contain both inference contexts:

- `ACTIVE`: the suspicious topology forms after the weekly snapshot and is
  represented by strictly prior live causal-context features;
- `ESTABLISHED`: the relevant relationships are already present in immutable
  graph history and can affect learned embeddings.

`S0` targets are necessarily `ACTIVE` because no earlier corpus segment exists.
For `S1` through `S4`, each specialist's ten positives per segment contain
exactly five `ACTIVE` and five `ESTABLISHED` examples.

## 10. Leakage and shortcut controls

1. Target status is not a live target feature. Fraud outcomes are distributed
   across `Rejected`, `Approved`, and `Paid` so outcome status cannot identify a
   family.
2. Names, descriptions, categories, amounts, departments, and timestamps must
   not contain pattern names or scenario identifiers.
3. Each specialist uses multiple disjoint actor groups across segments.
4. The same actor may participate in ordinary activity outside a fraud
   scenario, preventing identity memorization.
5. Fraud and hard-negative descriptions use the same templates and vocabulary.
6. Category and amount distributions are matched across all six fraud families
   and their legitimate controls.
7. No nomination ID, stable scenario ID, disposition, or metadata field enters
   model features.
8. Graph Analytics output is generated only after the corpus has been committed
   and never modifies its truth metadata.

## 11. Legitimate hard-negative allocation

Reserve at least ten explicit hard-negative targets per specialist per segment,
for a minimum of 300 specialist-focused hard negatives. They remain part of the
14,700 legitimate nominations and do not change the 98/2 class balance.

All hard negatives use `scenario_family = LEGITIMATE` and
`confirmed_patterns = []`. The persisted metadata does not contain a back-map
such as `decoy_for_patterns`. Their `scenario_variant` records the legitimate
business situation only; it is never interpreted as a specialist label.

### 11.1 Make graph candidates learn graph value

Clearing label-count gates is insufficient. Every specialist must also show
message-passing value over its causal-feature MLP. For that reason, each fraud
scenario is paired with legitimate controls whose scalar feature-contract
values are deliberately similar while their broader graph context differs.

Examples:

- legitimate reciprocal recognition matches prior reverse-pair counts but sits
  in an otherwise broad, ordinary collaboration network;
- legitimate closed paths match two-hop or three-hop path counts but lack the
  repeated coordinated neighborhood of fraudulent rings;
- legitimate deadline activity matches one-hour and 30-day burst counts but is
  distributed across ordinary organizational relationships;
- legitimate culture champions and popular recipients match local degree and
  breadth counts but connect diverse, non-collusive communities; and
- legitimate cross-functional campaigns match dense-block local counts while
  connecting established project communities rather than an isolated campaign.

This prevents the causal MLP from winning solely on obvious count thresholds
and gives GraphSAGE, GCN, and GATv2 meaningful graph structure on which to
compete. It is validation data engineered to prove the specialist machinery,
not evidence of expected production accuracy.

### 11.2 Required model outcome

V4 is not accepted merely because all label gates pass. A complete validation
run must produce, for each of the six behavior tracks:

- at least one eligible graph candidate;
- a selected architecture from GraphSAGE, GCN, or GATv2;
- final admission over the causal MLP under the configured margin;
- a published specialist artifact and calibration contract; and
- an active specialist reference in the serving bundle.

If a family fails `NO_MESSAGE_PASSING_VALUE_OVER_MLP`, the scenario and matched
controls must be reviewed for missing graph information or scalar-feature
shortcuts. The admission threshold must not be lowered merely to force a
winner.

## 12. Reset boundary

The reset preserves:

- `dbo.Tenants` and all tenant configuration;
- all 401 SQL and Entra user identities;
- roles and the operational administrator;
- categories and email templates;
- Graph and GNN scoring-policy history; and
- DNS, application registrations, and site configuration.

It removes every nomination and decision belonging to TenantId 5, including
test nominations created after the v3 corpus, plus artifacts derived from that
tenant's nomination history. This includes logs, graph findings, user graph
flags, embeddings, component serving pointers, and other documented dependent
rows.

The checked-in `reset_synthetics_inc_corpus.sql` is pinned to TenantId 5 and
fails closed unless all of these identity values match:

| Reset preflight | Expected value |
|---|---|
| SQL tenant | `TenantId = 5` |
| Organization ID | `f74bff31-f42f-4461-a1dd-e1ae978c1abe` |
| Tenant name | `Synthetics Inc` |
| Domain | `synthetic-awards.terianix.ai` |
| Synthetic marker | `is_synthetic = 1` |
| Preserved SQL users | 401 |

The safe replacement sequence is:

1. stop or verify the absence of running analytics and integrity jobs;
2. preserve the deployed v3 manifest as reset provenance;
3. run the tenant-scoped reset in rollback-preview mode;
4. verify exactly 401 preserved users and zero remaining nominations and
   decisions;
5. rerun the same script in commit mode;
6. run the v4 generator locally in dry-run and validation modes;
7. compare its expected SHA-256 and quotas with the reviewed manifest;
8. run `--apply-corpus` so the existing Entra directory is not recreated;
9. validate SQL identity, label, temporal, and metadata counts;
10. run Graph Analytics and `fraud-analytics-job` only after corpus validation;
11. verify the new immutable model artifacts before testing live inference.

## 13. Generator and validation changes

The implementation includes:

- update `GENERATOR_VERSION`, seed defaults, and manifest name to v4;
- replace the broad v3 family allocation with the exact allocation in section
  5;
- allow a causal scenario to own a variable number of precursor rows;
- implement independent topology recipes for all six specialists;
- write metadata schema version 3 with `confirmed_patterns` on every row;
- add feature-matched legitimate controls without persisting a specialist
  back-map;
- remove the synthetic `scenario_family` compatibility map from the label
  loader after the v3 corpus is deleted;
- fail v4 validation when `confirmed_patterns` is absent, malformed, empty on a
  fraud target, contains multiple values, or differs from `scenario_family`;
- validate exact per-segment and per-pattern quotas;
- validate each confirmed topology at its target cutoff;
- validate that all unconfirmed specialist labels remain absent;
- verify expected rolling-fold label evidence before database writes;
- execute the complete specialist training/admission test before accepting the
  corpus;
- update the reset preflight for the deployed v3 corpus; and
- update the package README and commands to use the v4 manifest.

No database schema migration is required because
`TrainingDispositionMetadataJson` already stores the normalized pattern array.

## 14. Required manifest diagnostics

The v4 manifest must include:

```json
{
  "pattern_taxonomy_version": "gnn-v3-patterns-v1",
  "confirmed_pattern_counts": {
    "RING": 50,
    "RECIPROCAL": 50,
    "TEMPORAL_BURST": 50,
    "SUPER_NOMINATOR": 50,
    "SUPER_BENEFICIARY": 50,
    "BIPARTITE_DENSE_BLOCK": 50
  },
  "fold_label_evidence": {
    "BIPARTITE_DENSE_BLOCK": {
      "training_positive_count": 30,
      "selection_fold_positive_counts": [10, 10],
      "final_holdout_positive_count": 10
    }
  }
}
```

The same fold evidence is required for all six specialists. The manifest also
records legitimate-control counts, context-mode counts, temporal boundaries,
generator version, seed, corpus hash, and generation run ID.

The implemented reference dry run for seed `20260921` and exclusive boundary
`2026-09-22` produces:

| Reproducibility field | Value |
|---|---|
| Directory seed | `20260912` |
| Corpus SHA-256 | `f296b8cde2b363e87813a7078c7b486d29bce57ff176808a95f34b7836a7f196` |
| Generation run ID | `b7a45f66-fb79-5e9d-894a-6184b584735f` |
| Causal scenarios | 600 |
| Causal precursor nominations | 4,520 |

These values describe the generator output, not a deployed corpus. The
`--apply-corpus` manifest must match them before the new data is accepted.

## 15. Post-load SQL acceptance checks

The persisted label distribution must be independently queryable without the
Python generator. A representative check is:

```sql
SELECT
    CAST(JSON_VALUE(
        idr.TrainingDispositionMetadataJson,
        '$.temporal_segment'
    ) AS INT) AS TemporalSegment,
    pattern.[value] AS ConfirmedPattern,
    COUNT_BIG(*) AS LabelCount
FROM dbo.IntegrityDecisionResults AS idr
CROSS APPLY OPENJSON(
    idr.TrainingDispositionMetadataJson,
    '$.confirmed_patterns'
) AS pattern
WHERE idr.TenantId = (
    SELECT TenantId
    FROM dbo.Tenants
    WHERE AzureAdTenantId = 'f74bff31-f42f-4461-a1dd-e1ae978c1abe'
)
  AND idr.TrainingDisposition = 'FRAUD'
  AND idr.TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH'
GROUP BY
    CAST(JSON_VALUE(
        idr.TrainingDispositionMetadataJson,
        '$.temporal_segment'
    ) AS INT),
    pattern.[value]
ORDER BY TemporalSegment, ConfirmedPattern;
```

Expected result: every segment contains exactly 10 rows for every confirmed
pattern. Every fraud target has exactly one pattern, so the sum of the pattern
counts is exactly the 300 unique fraud targets.

## 16. Acceptance criteria

V4 is ready for model training only when all of the following pass:

- exactly 400 corpus users plus one operational administrator are preserved;
- exactly 15,000 nominations and decisions are inserted;
- exactly 14,700 dispositions are `LEGITIMATE` and 300 are `FRAUD`;
- every row has metadata schema version 3 and a valid `confirmed_patterns`
  array;
- each fraud target has exactly one pattern equal to its `scenario_family`;
- each legitimate row has `scenario_family = LEGITIMATE` and no pattern;
- each specialist has exactly 50 positives and 10 positives per segment;
- each specialist has two evaluable selection folds and 10 final-holdout
  positives;
- every specialist has the required hard-negative coverage;
- no target consumes future topology or its own edge;
- the generated manifest is reproducible from the fixed inputs;
- reset and seed operations remain strictly tenant-scoped; and
- no Graph, GNN, Tabular, or Semantic result was used to create a label;
- every specialist selects and admits a graph architecture; and
- the serving bundle contains six active specialist artifacts.

The SQL and generator checks are prerequisites for running
`fraud-analytics-job`. The corpus is not considered a successful specialist
validation corpus until all six tracks also pass model admission. A failure is
diagnostic evidence to improve topology or matched controls, not permission to
introduce a label mapping or weaken admission policy.
