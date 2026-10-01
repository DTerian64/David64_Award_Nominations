# Integrity Analytics Multi-System Packaging Sprint

**Status:** Implemented locally; sandbox validation pending
**Decision date:** 2026-09-30  
**Applies to:** `fraud-analytics-job`  
**Initial system:** Award Nomination  
**Future systems:** Payroll, HR, and other integrity event sources

## 1. Objective

Restructure `fraud-analytics-job` so that source-specific ingestion, feature
construction, and pipeline orchestration are packaged by system. The first
implementation moves the existing Award Nomination pipeline into a vertical
system package without changing its runtime behavior, database schema, model
selection, artifacts, or schedule.

The structure must support:

- adding a new source system without spreading its implementation across many
  unrelated top-level packages;
- training and running models independently for each system;
- continuing to use one shared Integrity Sentinel database;
- sharing model algorithms and canonical contracts where their semantics are
  genuinely common; and
- introducing cross-system identity and integrity decisions later without
  coupling one source adapter directly to another.

## 2. Architectural decision

Use a hybrid, system-oriented structure:

```text
fraud-analytics-job/
├── systems/
│   ├── award_nominations/
│   │   ├── source/
│   │   ├── features/
│   │   │   ├── tabular/
│   │   │   ├── graph/
│   │   │   └── gnn/
│   │   ├── modeling/
│   │   ├── forecasting/
│   │   ├── pipeline.py
│   │   └── contracts.py
│   ├── payroll/
│   └── hr/
├── integrity_data/
├── integrity_sentinel/
├── modeling/
├── orchestration/
└── utils/
```

This is preferred over four parallel system trees such as
`source_adapters/{system}`, `feature_builders/{system}`,
`integrity_sentinel/{system}`, and `integrity_data/{system}`. A vertical system
package keeps the complete implementation of a new system discoverable while
the shared packages retain stable, source-neutral responsibilities.

## 3. Package responsibilities

### 3.1 `systems/{system_name}`

Owns everything that knows the source system's storage model or business
semantics:

- source connection and extraction;
- source-to-canonical mapping;
- source capabilities and tenant configuration;
- system-specific feature projections;
- system-specific pipeline composition;
- system-specific model-stage orchestration;
- source-owned forecasting or maintenance jobs; and
- source-specific smoke tests.

For Award Nomination, this package knows concepts such as `Nominations`,
`NominatorId`, `BeneficiaryId`, nomination status, categories, and Award tenant
configuration.

### 3.2 `integrity_data`

Remains source-neutral. It owns the canonical contracts and their validation:

- `Actor`;
- `IntegrityEvent`;
- `EventParticipant`;
- `Relationship`;
- `OutcomeLabel`;
- `DatasetSnapshot`; and
- canonical hashing and time-boundary rules.

There will not be an `integrity_data/award_nominations` package in the initial
design. Award-specific mapping belongs under
`systems/award_nominations/source`. The same rule will apply to Payroll and HR.

### 3.3 `integrity_sentinel`

Owns shared access to the Integrity Sentinel database and all runtime SQL for
`integrity.*` and `ops.*`:

- database connections;
- reviewed outcomes;
- model policies;
- component and serving status;
- run coordination, leases, and stage attempts;
- shared model registry operations; and
- shared persisted projections.

It must not import an Award, Payroll, or HR source adapter. A system pipeline
may call Sentinel services, but Sentinel services must remain unaware of the
calling system's physical source database.

### 3.4 `modeling`

The root `modeling` package owns only proven cross-system artifact utilities.
Award-specific Tabular and GNN algorithms, temporal evaluation, candidate
selection, admission gates, preprocessing, and artifact assembly live under
`systems/award_nominations/modeling` because their contracts encode Award
entities and behavior semantics.

### 3.5 `orchestration`

Will own the job runner, system-pipeline registry, work-item contract, and
multi-replica execution flow. During this sprint, the existing `run_job.py`
entry point may remain at the package root while its stage registry is changed
to load the Award system package.

## 4. Dependency direction

The allowed dependency direction is:

```text
orchestration
    -> systems/{system}
        -> integrity_data
        -> integrity_sentinel services
        -> shared modeling
        -> shared utilities

integrity_sentinel -> integrity_data and shared utilities
shared modeling    -> integrity_data and feature contracts
```

The following dependencies are prohibited:

- `integrity_sentinel` importing `systems/{system}`;
- shared modeling importing a source adapter;
- one system package importing another system package;
- source packages querying `integrity.*` or `ops.*`;
- Sentinel packages querying source-owned `dbo.*`; and
- shared packages using Award-specific table or column names.

## 5. Shared Integrity Sentinel database

All systems will use the same Integrity Sentinel database. This is the shared
control plane, not a shared source database.

Initially, `AWARD_SQL_*` and `IS_SQL_*` point to the same physical database.
The package boundary must nevertheless treat them as separate connections so
that `integrity.*` and `ops.*` can later move without changing Award extraction
or model algorithms.

Future source systems will have their own connection pairs, for example:

```text
PAYROLL_SQL_SERVER
PAYROLL_SQL_DATABASE

HR_SQL_SERVER
HR_SQL_DATABASE
```

They will continue to use the shared `IS_SQL_SERVER` and `IS_SQL_DATABASE` for
Sentinel state.

## 6. Independent system execution

Each source system should eventually have an independently schedulable
execution, even when executions use the same container image:

```text
award-integrity-analytics   SYSTEM=award_nominations
payroll-integrity-analytics SYSTEM=payroll
hr-integrity-analytics      SYSTEM=hr
```

The future work-item identity is:

```text
TenantId + SourceSystem + Pipeline + DataAsOfUtc
```

This prevents a slow or failed Payroll run from blocking Award processing and
allows each source to use an appropriate cadence, timeout, model policy, and
tenant enablement switch.

Changing the work-item identity and the `ops.*` schema is not part of this
packaging sprint.

## 7. Sprint scope

This sprint is a behavior-preserving package migration for Award Nomination.

### 7.1 In scope

- create `systems/award_nominations`;
- move Award source connection, extraction, mapping, capabilities, and tenant
  configuration into its `source` package;
- move Award-specific canonical projections into its `features` package;
- move Award-specific Graph, Tabular, and GNN stage orchestration into its
  `modeling` package;
- move Award forecasting and holiday synchronization into its system package;
- move Award dataset assembly out of `integrity_sentinel` and into the Award
  pipeline;
- update `run_job.py` stage registration to use the new system package;
- update existing callers and tests in the same coordinated cutover;
- place Award artifacts below `tenant_<id>/awards/` and update producers and
  consumers in the same coordinated cutover;
- add dependency-boundary tests; and
- update architecture documentation and operational commands.

### 7.2 Out of scope

- adding Payroll or HR ingestion;
- changing database schemas;
- changing model inputs, hyperparameters, selection, or admission rules;
- changing artifact payload or manifest formats;
- changing the Container Apps Job schedule or replica behavior;
- changing tenant enablement behavior;
- splitting the physical Award and Sentinel databases;
- changing backend or `integrity-check` database connections;
- implementing cross-system entity matching; and
- training a model with combined Award and Payroll features.

## 8. Initial file-migration plan

The final filenames may be refined during implementation, but ownership should
follow this mapping.

| Current location | Target ownership |
|---|---|
| `source_adapters/award_nominations/*` | `systems/award_nominations/source/*` |
| `feature_builders/source_views.py` | `systems/award_nominations/features/source_views.py` |
| `feature_builders/tabular/award_nomination_tabular_v1.py` | `systems/award_nominations/features/tabular/` |
| `feature_builders/tabular/category_encoding.py` | `systems/award_nominations/features/tabular/category_encoding.py` |
| `integrity_sentinel/datasets.py` | `systems/award_nominations/pipeline.py` or `dataset.py` |
| Award Graph stage orchestration | `systems/award_nominations/modeling/graph.py` |
| Award Tabular stage, algorithms, selection, and artifacts | `systems/award_nominations/modeling/tabular/` |
| Award GNN stage, algorithms, evaluators, and artifacts | `systems/award_nominations/modeling/gnn/` |
| Award forecasting and holiday sync | `systems/award_nominations/forecasting/` |

The following remain shared:

| Shared package | Reason |
|---|---|
| `integrity_data` contracts, snapshots, and validation | Canonical source-neutral data model |
| `integrity_sentinel/db.py` | Sentinel connection boundary |
| Sentinel outcome, status, policy, and coordination services | Shared database ownership |
| model artifact utilities | Shared publication mechanics |
| stage-result and SQL connection primitives | Shared infrastructure |

Database-free model code is not necessarily system-neutral. The Tabular
implementation assumes Award nomination identities, timestamps, category
encoding, and label sources. The GNN implementation assumes the Award
user/nomination/category topology, nominator and beneficiary endpoints, and the
Award behavior taxonomy. Both complete implementations therefore belong to the
Award system package. Generic numerical or evaluation primitives should be
extracted only after another system demonstrates a matching contract;
speculative shared abstractions are not required.

## 9. Cutover strategy

This sandbox migration uses a strict one-step package cutover. Production,
tests, scripts, and operational documentation import Award functionality only
through `systems.award_nominations`. The former Award-specific modules under
`source_adapters`, `feature_builders`, `modeling`, `misc_jobs`, and
`integrity_sentinel` are removed rather than retained as compatibility aliases.

Model classes serialized directly into serving artifacts must not be moved
merely for package symmetry. The current Tabular bundle serializes the fitted
scikit-learn model and preprocessing state rather than the Award training
wrapper classes, while GNN bundles are weights-only tensor contracts. Tests
verify that these package moves do not add the training package path to serving
artifacts.

## 10. Acceptance criteria

The sprint is complete when:

1. The scheduled job runs Award Graph, Tabular, GNN, and Forecast stages from
   `systems.award_nominations`.
2. Award-owned `dbo.*` SQL exists only inside the Award system package.
3. `integrity.*` and `ops.*` SQL remains inside `integrity_sentinel`.
4. Shared modeling packages contain no Award table names or source connection
   code.
5. Dataset snapshots preserve their source system, adapter version, record
   counts, cutoff, and deterministic hash behavior.
6. Award artifacts use `tenant_<id>/awards/{tabular,gnn,graph}/...`, while
   serving-version resolution remains unchanged.
7. Existing stage reason codes and run-history behavior remain unchanged.
8. Multi-replica tenant claiming and lease fencing remain unchanged.
9. The full `fraud-analytics-job` test suite passes.
10. Terraform configuration remains unchanged unless a package path affects the
    image build or runtime command.
11. A container image imports every production stage successfully.
12. Legacy Award module paths are absent and repository callers use the new
    system package directly.

## 11. Verification plan

- run all unit and contract tests;
- run database-boundary tests that inspect package ownership;
- import every production stage using the same module paths used by
  `run_job.py`;
- build the analytics container image;
- run a filtered local or sandbox tenant execution;
- compare stage-attempt count, statuses, reason codes, and diagnostics with the
  previous package layout;
- confirm produced artifact payloads are unchanged and blob paths use the
  `tenant_<id>/awards/` namespace; and
- run one manual two-replica sandbox execution after deployment.

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Import cycles between system, Sentinel, and modeling packages | Enforce the dependency direction with import-boundary tests |
| Existing serving versions point to legacy tenant-only blob paths | Before the consumer cutover, copy each active incumbent Tabular and GNN bundle into `tenant_<id>/awards/`, or successfully republish every active component and verify its system-scoped bundle |
| Stale imports survive the package migration | Boundary tests assert that legacy alias files are absent |
| Tests monkeypatch old module globals | Patch the owning module and add new-path import tests |
| Dataset hashes change unintentionally | Preserve adapter identity and canonical records; compare snapshot tests |
| Award-specific model code is mistaken for a reusable abstraction | Extract shared algorithms only after a second system proves an identical contract |
| Over-generalizing before a second source exists | Use Award as the vertical slice and extract abstractions only at clear seams |

## 13. Estimated sprint effort

| Work item | Estimate |
|---|---:|
| Scaffold packages and boundary tests | 0.5 day |
| Move Award source and feature code | 1 day |
| Move Graph, Tabular, and GNN orchestration | 1.5–2 days |
| Move forecasting and update job registration | 0.5 day |
| Compatibility, regression fixes, and container verification | 1–1.5 days |
| **Estimated total** | **4–5.5 engineering days** |

The estimate assumes no database migration and no change to model behavior.
Artifact compatibility or hidden cross-package dependencies may extend the
verification work.

## 14. Follow-on roadmap

### Phase 2: source-aware orchestration

- introduce a system pipeline registry;
- make work items source-aware;
- allow independent schedules and tenant enablement per system; and
- record `SourceSystem` in operational run history.

### Phase 3: source-aware Sentinel schema

Evolve identities such as:

```text
NominationId
    -> SourceSystem + SourceEventId

TenantId + Component
    -> TenantId + SourceSystem + Component
```

Policies, findings, embeddings, stage attempts, and component status will need
equivalent source scoping.

### Phase 4: source-aware artifacts and serving

Use source-scoped artifact identities, for example:

```text
tenant_5/awards/tabular/<version>/
tenant_5/payroll/tabular/<version>/
tenant_5/cross_system/decision/<version>/
```

### Phase 5: second-source proof

Implement a narrow Payroll vertical slice, preferably beginning with canonical
events and one Tabular model. A second real source is the validation that the
package boundaries are reusable rather than renamed Award abstractions.

### Phase 6: cross-system integrity

Begin with late fusion of independently produced findings and scores. Add an
explicit, time-valid identity mapping before constructing combined features or
cross-system graphs:

```text
TenantId
SourceSystem
SourceActorId
CanonicalEntityId
ValidFrom
ValidTo
Confidence
MappingProvenance
```

Raw identifiers from different systems must never be assumed to represent the
same person merely because their values match.

## 15. Completion decision

This sprint ends after the Award Nomination pipeline is operating from its new
vertical package with unchanged behavior. Source-aware schemas, a second
adapter, and cross-system modeling begin only in their corresponding follow-on
phases.

## 16. Implementation note

The package migration was implemented on 2026-09-30. Production stage
registration and all repository callers now use `systems.award_nominations`;
legacy alias modules were removed. Graph and GNN persistence was separated into
`integrity_sentinel.graph_store` and `integrity_sentinel.gnn_store`, keeping
all `integrity.*` and `ops.*` SQL under Sentinel ownership.

One sandbox execution still needs to validate every tenant stage and both
global preparation steps. Local verification completed after the strict cutover
with all 173 `fraud-analytics-job` tests passing.
