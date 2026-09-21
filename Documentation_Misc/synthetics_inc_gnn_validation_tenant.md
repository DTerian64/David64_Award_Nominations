# Synthetics Inc. — GNN Validation Tenant Design and Build Runbook

> **Replacement note (2026-09-21):** This document describes the deployed v3
> corpus. The proposed clean replacement, with direct one-to-one GNN specialist
> labels and no scenario compatibility mapping, is defined in
> `Documentation_Misc/synthetics_inc_specialist_corpus_v4.md`.

## 1. Purpose

Synthetics Inc. is a dedicated, isolated tenant for developing and validating
the tenant-specific GNN training, architecture-selection, inference, and
explanation pipeline against deterministic synthetic ground truth.

The tenant is not a source of evidence about production accuracy. Its results
prove that the system behaves correctly when the truth is known: temporal graph
construction, label isolation, candidate comparison, artifact publication,
serving, and explanation reproducibility.

Synthetic users and nominations must never be inserted into a real customer's
tenant. Every generated row remains inside the Synthetics Inc. tenant boundary.

## 2. Fixed tenant identity

| Property | Value |
|---|---|
| Organization name | `Synthetics Inc` |
| Provided organization identifier | `f74bff31-f42f-4461-a1dd-e1ae978c1abe` |
| Expected internal name | `Synthetics Inc` |
| Canonical hostname (`Domain`) | `synthetic-awards.terianix.ai` |
| Site URL (`Site_URL`) | `https://synthetic-awards.terianix.ai` |
| Logo path in the SPA | `/synthetics-inc-logo.png` |
| Public logo URL | `https://synthetic-awards.terianix.ai/synthetics-inc-logo.png` |
| Suggested tagline | `Recognition, intelligently composed.` |
| Corpus users | 400 synthetic users |
| Operational administrator | `David64 Terian` |
| Total destination user rows | 401: 400 corpus users plus 1 administrator |
| User UPN suffix | `@synthetics.terian-services.com` |
| Corpus nominations | 15,000 synthetic nominations |
| Ground-truth prevalence | 98% legitimate / 2% fraud |
| Synthetic history | 365 days |
| Generator version | `synthetics-inc-v3.0` |
| Deterministic seed | `20260912` |
| Exclusive as-of boundary | `2026-09-14T00:00:00Z` |

The database `TenantId` must be discovered from the organization identifier;
scripts and migrations must not assume that it is numerically `4`. Before
writing the tenant row, confirm that the provided organization identifier is
the Entra tenant identifier expected by `dbo.Tenants.AzureAdTenantId`.

## 3. Design principles

1. **Strict tenant isolation.** Every user referenced by a nomination must
   belong to Synthetics Inc. Cross-tenant references abort the load.
2. **Truth precedes observations.** The scenario generator chooses the hidden
   ground truth before creating descriptions, statuses, decisions, or model
   outputs.
3. **No model-generated labels.** RF, Graph Analytics, GNN, semantic checks, and
   LLM output may not create or modify synthetic ground truth.
4. **No fabricated inference.** Imported historical rows record that engines
   were not run; the loader never invents RF, Graph, GNN, or semantic scores.
5. **Temporal integrity.** Every feature and graph edge available to a training
   target must predate that target. Scenarios explicitly distinguish topology
   already present in a weekly snapshot from topology forming after that
   snapshot. The final temporal segment remains an untouched holdout during
   candidate selection.
6. **Determinism.** The same generator version, seed, configuration snapshot,
   and empty destination tenant produce the same logical users, nominations,
   labels, and temporal allocations.
7. **Idempotency.** A normal rerun makes no duplicates. Reset requires an
   explicit tenant identity confirmation and affects only rows owned by this
   synthetic tenant.
8. **Visible provenance.** Synthetic training metrics and explanations must be
   visibly labeled synthetic in the UI and exported artifacts.

## 4. Required schema and label-contract work

This work must be deployed before loading ground truth.

### 4.1 Do not reuse `is_demo`

Add a tenant-level flag:

```sql
is_synthetic BIT NOT NULL DEFAULT 0
```

Set `is_synthetic = 1` only for Synthetics Inc. Keep `is_demo` copied from
Tenant 1—normally `0`—unless the public demo-registration flow is first made
tenant-specific.

This separation is required because the current demo-registration helpers query
`WHERE is_demo = 1` without a tenant discriminator and assume that exactly one
demo tenant exists. Setting a second tenant to `is_demo = 1` would make public
demo registration, invitation redirects, and demo branding ambiguous.

### 4.2 Add explicit training-label provenance

Extend `dbo.IntegrityDecisionResults` with:

```sql
TrainingDispositionSource       VARCHAR(40)   NULL
TrainingDispositionMetadataJson NVARCHAR(MAX) NULL
```

Initial allowed sources:

| Source | Meaning |
|---|---|
| `HUMAN_INVESTIGATION` | A real HRBP investigation determined the outcome |
| `RANDOM_AUDIT` | A randomized human audit determined the outcome |
| `SYNTHETIC_GROUND_TRUTH` | A deterministic synthetic scenario defined the outcome |

`TrainingDispositionMetadataJson` for synthetic rows must contain at least:

```json
{
  "schema_version": 2,
  "generator_version": "synthetics-inc-v3.0",
  "generation_run_id": "<uuid>",
  "seed": 20260912,
  "scenario_id": "<stable-id>",
  "scenario_family": "LEGITIMATE|RING|RECIPROCAL|CONCENTRATION|BURST|AMOUNT|MIXED",
  "scenario_phase": "BACKGROUND|PRECURSOR|TARGET",
  "context_mode": "NONE|ACTIVE|ESTABLISHED",
  "ground_truth": "LEGITIMATE|FRAUD"
}
```

`scenario_id` is populated for the two precursors and target that make up a
causal scenario. Ordinary background legitimate rows use `null`, with
`scenario_phase = 'BACKGROUND'` and `context_mode = 'NONE'`.

The integrity-decision constraints must allow a synthetic disposition without
claiming that a human reviewed it:

- `TrainingDisposition` is `FRAUD` or `LEGITIMATE`;
- `TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH'`;
- `HumanReviewOutcome`, `ReviewedBy`, and `ReviewedAt` remain `NULL`;
- metadata is valid JSON and contains generator and scenario identity; and
- the owning tenant has `is_synthetic = 1`, enforced by the write service and
  rechecked by the GNN loader.

The cross-table `is_synthetic` rule cannot be expressed as an ordinary SQL
Server `CHECK` constraint. The seeder and training loader must both enforce it.

### 4.3 Update the canonical label loader

Replace the GNN-specific assumption that every eligible label is HRBP-confirmed
with a model-neutral eligible-label rule:

- real tenants: accept `HUMAN_INVESTIGATION` and approved `RANDOM_AUDIT` labels;
- synthetic tenants: additionally accept `SYNTHETIC_GROUND_TRUTH`;
- never accept a synthetic source when `is_synthetic = 0`;
- never consume `EXCLUDED`, `NULL`, RF bootstrap labels, model predictions, or
  semantic-only outcomes as GNN targets; and
- preserve label-source counts in training diagnostics and `metrics.json`.

## 5. Tenant configuration cloning

Tenant 1 is the source configuration. Clone values through a versioned,
transactional seeder—not through a one-time collection of hand-edited SQL.

### 5.1 Clone from Tenant 1

| Configuration | Treatment |
|---|---|
| `dbo.Tenants.Config` | Exact JSON clone |
| `dbo.Tenants.desc_check_config` | Exact JSON clone |
| `dbo.Tenants.certificate_config` | Exact JSON clone |
| `dbo.Tenants.integrity_config` | Exact JSON clone; retained compatibility fields included |
| `dbo.nomination_categories` | Clone active and inactive rows, descriptions, amount limits, and active state; generate new category IDs |
| `dbo.EmailTemplates` | Clone templates and languages with new identities and TenantId |
| Active `dbo.GraphScoringPolicies` row | Clone into a new version-1 active policy for Synthetics Inc. |
| `dbo.GraphScoringPatternParameters` | Clone under the newly generated graph PolicyId |
| Active `dbo.GNNScoringPolicies` row | Clone into a new version-1 active policy, then apply the documented 365-day override |

All copied rows receive new identity keys, the destination TenantId, and audit
actors such as `svc:synthetic-tenant-seeder:v1`. Foreign keys must be remapped;
identity values from Tenant 1 must never be reused.

### 5.2 Tenant-specific overrides

| Field | Override |
|---|---|
| `TenantName` | `Synthetics Inc` |
| `AzureAdTenantId` | `f74bff31-f42f-4461-a1dd-e1ae978c1abe`, after preflight confirmation |
| `Domain` | `synthetic-awards.terianix.ai` |
| `Site_URL` | `https://synthetic-awards.terianix.ai` |
| `Company_Logo_URL` | `https://synthetic-awards.terianix.ai/synthetics-inc-logo.png` |
| `Tagline` | `Recognition, intelligently composed.` |
| `is_synthetic` | `1` |
| GNN `ConfigurationJson.training.window_days` | `365` |

Keep all other GNN parameters, candidate architectures, selection rules,
thresholds, and explanation settings equal to Tenant 1 for the first run.
Training may remain disabled during data loading and be enabled only after the
validation queries pass.

### 5.3 Never clone from Tenant 1

Do not copy tenant identity, operational state, or secrets:

- users, roles, nominations, decisions, logs, conversations, and audit history;
- RF/GNN artifacts, embeddings, model versions, component status, forecasts,
  graph snapshots, graph findings, or change requests;
- payroll providers, tokens, submissions, or provider IDs;
- Service Bus processed-message state;
- fallback administrator email or other notification destinations; or
- any Tenant 1 identity key, secret, external account ID, or user address.

Set `payroll_provider_id = NULL`. Route email to a deliberate non-delivery sink
or disable notification publication during the bulk seed. Never copy Tenant 1's
fallback administrator address.

## 6. Branding

The approved logo asset is:

```text
frontend/public/synthetics-inc-logo.png
```

It is a transparent 1254×1254 PNG containing an abstract S-shaped synthetic
network in indigo, violet, and cyan. The mark contains no generated text, so the
application supplies the accessible organization name separately.

Deployment requirements:

1. Deploy the frontend asset.
2. Confirm that `/synthetics-inc-logo.png` returns `200` from the custom host.
3. Set `Company_Logo_URL` to the absolute public URL.
4. Verify the logo at desktop, mobile, login, nomination, setup, and certificate
   sizes, including white and light-gray backgrounds.
5. Preserve the transparent original; create separately named derivatives if a
   smaller optimized size is later required.

## 7. User population

### 7.1 Population contract

Create exactly 400 synthetic corpus users plus one operational administrator,
`David64 Terian`. The resulting destination roster contains 401 user rows. All
400 corpus users have first and last names that are Latin transliterations of
Armenian names; the administrator is the one explicit naming exception.

Names are a human-facing cue only. Every destination user must exist both as a
Microsoft Entra user in organization
`f74bff31-f42f-4461-a1dd-e1ae978c1abe` and as a corresponding `dbo.Users` row.
Tenant identity, provenance, and exclusion of the administrator from the
generated nomination graph provide the actual machine-enforced separation.

Use these identity rules:

- stable generator key: UUIDv5 from generator namespace plus logical user index,
  retained in the generation manifest;
- display name: unique first-name/last-name combination;
- UPN: `firstname.lastname@synthetics.terian-services.com`;
- email: `NULL` for corpus users unless an explicitly controlled non-production
  delivery sink is configured;
- a Microsoft Entra member account with `accountEnabled = false`;
- no mailbox license, invitation, interactive password use, or notification
  delivery;
- `created_by` and `updated_by`: `svc:synthetic-tenant-seeder:v1`; and
- every manager, nominator, beneficiary, and approver belongs to Synthetics Inc.

The `synthetics.terian-services.com` domain must be verified in the Entra tenant
before the 400 matching accounts are provisioned. All corpus accounts are
disabled login identities used for directory representation and safe admin
impersonation. They are not mail-enabled. If later agent testing requires direct
interactive sign-in, enable only a small separately approved subset and document
the credential and cleanup controls.

The application currently resolves users by UPN rather than an Entra object-ID
column on `dbo.Users`. The generation manifest must therefore retain each Entra
object ID, UPN, logical synthetic ID, and database UserId, while the deployed
application mapping remains the exact normalized UPN.

The administrator contract is:

| Field | Value |
|---|---|
| `FirstName` | `David64` |
| `LastName` | `Terian` |
| `userPrincipalName` | `david64.terian@synthetics.terian-services.com` |
| `Title` | `Executive Leadership` |
| Corpus eligibility | Excluded; never a seeded nominator, beneficiary, or approver |
| Entra account | Required and enabled for interactive administration |
| Administrative access | Entra app role `AWard_Nomination_Admin` |

Administrative access is Entra-managed, not granted through `dbo.UserRoles`.
The build process must resolve or create the Entra identity, assign the
`AWard_Nomination_Admin` app role, and ensure that the corresponding `dbo.Users`
row belongs to Synthetics Inc. Do not invent a deliverable email address for the
administrator; use the identity's approved address when it is known.

### 7.2 Microsoft Entra provisioning contract

The seeder provisions or reconciles all 401 destination identities through
Microsoft Graph before inserting dependent database rows:

1. Verify that `synthetics.terian-services.com` is a verified domain in the
   target Entra organization.
2. Acquire an application token scoped to organization
   `f74bff31-f42f-4461-a1dd-e1ae978c1abe`.
3. Resolve each UPN first; create only when no matching account exists.
4. Create the 400 corpus accounts as member users with `accountEnabled = false`,
   the Armenian display/given/surname values, and no licenses.
5. Populate Entra `department` with the approved department value. Populate
   `jobTitle` with the same value for consistency with `dbo.Users.Title` until
   the application gains a dedicated Department column.
6. Set Entra manager relationships after every required account exists.
7. Resolve or create the enabled `David64 Terian` account and assign the
   `AWard_Nomination_Admin` app role on the Award Nomination enterprise app.
8. Insert or reconcile `dbo.Users` by exact normalized UPN and record the Entra
   object-ID-to-UserId map in the generation manifest.
9. Compare Entra and SQL rosters in both directions; any missing, duplicate, or
   mismatched UPN aborts the nomination phase.

The provisioning application needs admin-consented Microsoft Graph application
permissions sufficient to create and update users and manager relationships.
Assigning `AWard_Nomination_Admin` additionally requires permission to read the
enterprise application and write app-role assignments. Follow the existing
`seed_demo.py` and `assign_admin_role.py` permission pattern:

```text
User.ReadWrite.All
Application.Read.All
AppRoleAssignment.ReadWrite.All
```

Do not print generated passwords, access tokens, object IDs associated with
unrelated users, or Graph response bodies containing directory data. Honor
Microsoft Graph `429` `Retry-After` responses and make the provisioning loop
safe to resume.

### 7.3 Organization shape

The 400 corpus users form a plausible hierarchy:

- one root executive with no manager;
- 8 division leads reporting to the root;
- 40–48 people managers distributed across the divisions;
- remaining users distributed across teams of 6–10;
- approximately 350–370 users participate meaningfully as nominators or
  beneficiaries;
- no large isolated-user population; and
- HRBP application roles may be assigned to a small corpus subset for workflow
  testing, but role assignment must not become a target label feature. David64
  Terian is the tenant administrator.

`David64 Terian` remains outside this hierarchy and has no nomination edges.

The generator must prevent self-nominations and normally select an approver from
the nominator's management chain.

### 7.4 Department values stored in `Users.Title`

The current schema does not have a dedicated department column. For this
synthetic tenant, `Users.Title` deliberately stores one of these 18 department
values:

```text
Sales
Finance
Information Technology
Cloud Computing
Legal
Human Resources
Marketing
Customer Success
Operations
Product Management
Engineering
Data & Analytics
Security
Compliance & Risk
Procurement
Research & Development
Professional Services
Executive Leadership
```

Every department must have a meaningful population, and manager assignments
should normally remain within a department before rolling up to a division lead.
Fraud and hard-negative actors must be distributed across departments so the
value in `Title` cannot become a label shortcut.

This is an explicit synthetic-data convention: the UI will display a department
where it ordinarily displays a job title. If a real `Department` field is added
later, migrate these values and restore `Title` to job-title semantics.

### 7.5 Armenian name pools

Store the curated pools in a version-controlled data file. The initial pool may
use the following transliterations.

First names:

```text
Anahit, Ani, Anna, Arevik, Armine, Astghik, Gayane, Gohar, Hasmik, Hripsime,
Karine, Lilit, Lusine, Mane, Mariam, Naira, Narine, Nune, Ruzanna, Shoghik,
Shushan, Siranush, Sona, Tatevik, Zara,
Aram, Armen, Artak, Artur, Ashot, Davit, Gagik, Gevorg, Gor, Grigor, Hayk,
Hovhannes, Karen, Levon, Mher, Narek, Ruben, Samvel, Sargis, Suren, Tigran,
Vahan, Vardan, Vahe, Zaven
```

Last names:

```text
Abrahamyan, Aleksanyan, Arakelyan, Asatryan, Avagyan, Avetisyan, Babayan,
Baghdasaryan, Balasanyan, Danielyan, Davtyan, Galstyan, Gasparyan, Gevorgyan,
Ghazaryan, Grigoryan, Hakobyan, Hambardzumyan, Harutyunyan, Hovhannisyan,
Isahakyan, Karapetyan, Khachatryan, Kirakosyan, Manukyan, Margaryan,
Martirosyan, Melikyan, Minasyan, Mirzoyan, Mkrtchyan, Muradyan, Nalbandyan,
Nersisyan, Ohanyan, Papazyan, Petrosyan, Poghosyan, Sahakyan, Sargsyan,
Shahinyan, Simonyan, Stepanyan, Ter-Petrosyan, Tumanyan, Vardanyan,
Yegiazaryan, Yeritsyan, Zakaryan, Zargaryan
```

Generate combinations without replacement and preserve stable spelling between
runs. Because each first-name/last-name pair is unique, the UPN can follow the
requested `firstname.lastname@synthetics.terian-services.com` format without a
numeric suffix. Normalize UPN characters to lowercase ASCII and fail rather than
silently adding a suffix if a collision is found.

`David64 Terian` is not part of the 400-user corpus and must never nominate,
approve, or receive a seeded nomination. Exclude the administrator from corpus
validation counts and from all random user selection. Additional real operator
rows require a design amendment; support-admin impersonation is preferred.

## 8. Nomination corpus

### 8.1 Volume and time

Generate exactly 15,000 nominations from `2025-09-14` through `2026-09-13`.
The fixed `2026-09-14T00:00:00Z` as-of instant is an exclusive boundary:

- 35 days contain 42 nominations;
- 330 days contain 41 nominations;
- dates and times are distributed across working hours with a small realistic
  weekend population;
- all timestamps are UTC and never in the future; and
- the chronological distribution is approximately uniform so date-based folds
  remain stable.

The rolling GNN policy uses five chronological segments for three folds:

```text
Time ---------------------------------------------------------------------->
        S0             S1             S2             S3             S4
   graph history   train fold 1   train fold 2   train fold 3   holdout
       3,000           3,000          3,000          3,000          3,000
```

Each 73-day segment contains exactly 2,940 legitimate and 60 fraudulent
nominations. The exact UTC date ranges are:

| Segment | Inclusive date range | Role |
|---|---|---|
| S0 | 2025-09-14–2025-11-25 | Graph history |
| S1 | 2025-11-26–2026-02-06 | Rolling train fold 1 |
| S2 | 2026-02-07–2026-04-20 | Rolling train fold 2 |
| S3 | 2026-04-21–2026-07-02 | Rolling train fold 3 |
| S4 | 2026-07-03–2026-09-13 | Final untouched holdout |

Expected supervised populations are therefore:

| Population | Legitimate | Fraud |
|---|---:|---:|
| Rolling train, S1–S3 | 8,820 | 180 |
| Final holdout, S4 | 2,940 | 60 |

S0 establishes graph history. Its truth remains auditable but does not become a
rolling training target under the current fold construction.

### 8.2 Category, amount, and description rules

- Use the newly cloned Synthetics Inc. category IDs, never Tenant 1 IDs.
- Respect tenant and category minimum/maximum award amounts.
- Pin the deterministic generator to the cloned Tenant 1 category bounds and
  fail closed during SQL preflight if the live cloned policy no longer accepts
  an amount. Each temporal segment contains 600 nominations from each of the
  five categories so category identity cannot act as a proxy for time/fold.
- Generate a realistic category-relative amount distribution with legitimate
  high-value and low-value examples.
- Every description names a specific behavior, outcome, and business impact.
- Include the exact award-category phrase naturally once so synthetic data does
  not fail merely because of the embedding-alignment threshold.
- Keep description quality comparable between fraud and legitimate examples;
  boilerplate, length, grammar, and vocabulary must not reveal the label.
- Do not place `synthetic`, `fraud`, scenario names, or label hints in user-facing
  descriptions.

### 8.3 Workflow states

This is a historical model-validation import, not a replay of 15,000 live Service
Bus submissions. Do not publish `nomination.submitted` events during the bulk
load and do not incur 15,000 LLM calls.

Use plausible historical statuses:

- legitimate nominations: predominantly `Paid` and `Approved`, with a small
  recent `Pending` population;
- confirmed historical fraud: a controlled mixture of `Rejected` outcomes and
  retrospectively discovered `Approved`/`Paid` outcomes; and
- no `PendingHRBPReview` or semantic-description rejection in the labeled
  training corpus.

Retrospectively discovered approved/paid fraud is useful because it can remain
in historical message-passing behavior while still carrying known ground truth.
The generator must not derive truth from status; status is created from truth
and scenario chronology.

## 9. Fraud and hard-negative design

### 9.1 Fraud allocation

Create exactly 300 unique fraudulent nominations. The allocation is:

| Scenario family | Fraud nominations | Primary signal |
|---|---:|---|
| Closed nomination rings | 90 | Multi-hop closed-cycle topology |
| Reciprocal/coordinated exchange | 60 | Counter-direction and repeated-pair history |
| Concentrated serial nominations | 60 | Unusually narrow beneficiary behavior |
| Coordinated bursts | 45 | Temporal clustering among related actors |
| Category-relative amount abuse | 30 | Amount behavior relative to category history |
| Mixed topology and amount | 15 | Multiple moderate signals |

Every temporal segment receives 60 fraud targets, with representation from
multiple families. Do not perform a Bernoulli 2% draw; exact quotas prevent a
fold from falling below the training gate.

### 9.2 Temporal scenario construction

Every one of the 300 fraud targets belongs to a stable causal scenario with
exactly two earlier, legitimate-looking precursor nominations. The target is
the first row at which the full suspicious condition is created or materially
strengthened. This prevents the label from describing topology that does not
yet exist when the target is scored.

The v3 corpus deliberately tests two inference contexts:

| Context | Target count | Precursor placement | Detection contract |
|---|---:|---|---|
| `ESTABLISHED` | 120 | Earlier immutable graph segment | Weekly user embeddings must carry the prior relationship signal |
| `ACTIVE` | 180 | Shortly before the target in its own segment | Live causal delta features must detect topology formed after the weekly snapshot |

S0 is graph-history warm-up and therefore assigns all 60 targets to `ACTIVE`.
Each of S1 through S4 contains 30 `ACTIVE` and 30 `ESTABLISHED` targets. Active
burst precursors and their target occur within five minutes; other active
precursors occur shortly before the target without deliberately introducing a
burst shortcut. Established precursors are placed far enough back to be inside
the immutable graph supplied to that rolling target.

Examples of the required causal shape are:

- ring: `A -> B`, then `B -> C`, then target `C -> A`;
- reciprocal: `A -> B`, then a benign supporting edge, then target `B -> A`;
- concentration: repeated `A -> B` history before another `A -> B` target;
- burst: two related nominations to the same beneficiary immediately before
  the target; and
- amount or mixed: established pair history before a category-relative amount
  outlier target.

The current weekly snapshot decoder cannot see `ACTIVE` precursors by itself.
The training and inference pipelines must add the same strictly-prior live
delta feature contract before v3 is eligible for deployment. Reseeding alone
does not provide that capability.

Reserve distinct actors and structures for S4 wherever practical. A model that
sees the same ring participants during training and holdout may memorize users
instead of learning transferable structure.

### 9.3 Required hard negatives

The legitimate population must include convincing look-alikes:

- open nomination chains that do not close into rings;
- legitimate reciprocal recognition separated in time;
- managers and culture champions with high nomination volume;
- mentors who repeatedly recognize a small group;
- project-launch recognition bursts;
- legitimate maximum-value awards with strong justification; and
- close collaborators whose concentration resembles scheme participants.

Balance flat user activity between fraud actors and their hard-negative peers.
Otherwise an MLP can solve the fixture from nomination counts and the test does
not prove that message passing contributes.

## 10. Synthetic historical decision envelope

Every labeled nomination needs an `IntegrityDecisionResults` row because that
table remains the canonical model-neutral outcome contract. For imported
history, persist an honest non-inference envelope:

- engine JSON documents: unavailable/not run with reason
  `SYNTHETIC_HISTORICAL_IMPORT`;
- `CompositeScore = NULL`;
- `CompositeRiskLevel = 'UNKNOWN'`;
- `DecisiveEnginesJson = []`;
- `FinalRoute = 'MANAGER_APPROVAL'` for `Pending`, `Approved`, and `Paid`
  history, or `HRBP_REVIEW` for a synthetic `Rejected` fraud outcome;
- `RoutingRule = 'SYNTHETIC_HISTORICAL_IMPORT'`;
- `ReviewScope = NULL` for manager-routed history, or `FRAUD` for a synthetic
  `Rejected` fraud outcome;
- `HumanReviewOutcome = NULL`;
- `TrainingDisposition = 'FRAUD'` or `'LEGITIMATE'` from hidden truth;
- `TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH'`; and
- `ScoredBy = 'svc:synthetic-tenant-seeder:v1'`.

The required migration must explicitly admit this envelope without weakening
the existing human-review invariants for real rows. No generated RF, Graph,
GNN, semantic, composite, or routing score is stored during import.

## 11. Seeder architecture

Implement the corpus as a dedicated version-controlled tool, proposed layout:

```text
scripts/synthetic_tenant/
  README.md
  seed_synthetics_inc.py
  armenian_names.json
  scenarios.py
  validation.py
  tests/
```

Required commands:

```powershell
python scripts/synthetic_tenant/seed_synthetics_inc.py --dry-run
python scripts/synthetic_tenant/seed_synthetics_inc.py --apply
python scripts/synthetic_tenant/seed_synthetics_inc.py --validate
```

Corpus replacement uses the guarded, manifest-pinned SQL runbook described in
section 15. The seeder itself deliberately has no destructive reset mode.

Required directory-provisioning configuration must be supplied through secrets
or workload identity, never committed configuration:

```text
SYNTHETICS_AAD_TENANT_ID=f74bff31-f42f-4461-a1dd-e1ae978c1abe
SYNTHETICS_GRAPH_CLIENT_ID=<provisioning app client ID>
SYNTHETICS_GRAPH_CLIENT_SECRET=<secret, when workload identity is unavailable>
```

Safety requirements:

- default mode is `--dry-run`;
- `--apply` refuses any destination without `is_synthetic = 1`;
- resolve destination by organization identifier and verify name and domain;
- `--reset` requires the exact organization identifier and a second check of
  `is_synthetic = 1`;
- use one database transaction per bounded phase, with an explicit checkpoint;
- write no secrets or real email addresses to output;
- perform bulk inserts without Service Bus publication;
- preserve generated logical IDs across retry; and
- emit a manifest containing generator version, seed, source configuration
  hashes, counts, time boundaries, scenario counts, and validation results.

The manifest belongs with generated test artifacts, not in GNN `metrics.json`.
The GNN metrics should reference its generation run ID and configuration hash.

## 12. Build sequence

Implementation status as of 2026-09-14: migration `0060`, tenant configuration,
directory/SQL users, and the 5,000-row v2.0 corpus are deployed. The expanded
15,000-row v3.0 generator is implemented and validated offline. The shared
`gnn-v2-causal-v1` training, serving, persistence, and explanation path is
implemented. The v2.0-to-v3.0 guarded replacement and a new analytics run remain
to be performed manually.

The fixed v3.0 corpus identity is:

- corpus SHA-256: `3883c2d69397564dc63286c786cd8cda0e2a43af2d5409fe42fb989737f880ed`;
- generation run ID: `e7d48607-01c2-580f-9c6c-100bf506e190`; and
- exclusive as-of boundary: `2026-09-14`.

### Phase A — schema and code safeguards

1. Add `Tenants.is_synthetic`.
2. Add training-disposition source and metadata fields.
3. Update integrity-decision constraints for the synthetic import envelope.
4. Update the canonical label loader and diagnostics.
5. Add tests proving synthetic labels are rejected for a non-synthetic tenant.
6. Add tests proving RF/Graph/GNN predictions never become synthetic truth.

### Phase B — tenant configuration

1. Resolve Tenant 1 and the destination organization by stable identifiers.
2. Create or update the Synthetics Inc. tenant row.
3. Clone allowed Tenant 1 configuration.
4. Copy categories and retain an old-to-new category ID map.
5. Clone email templates with delivery disabled or sinked.
6. Clone graph policy and child parameters with new policy IDs.
7. Clone GNN policy and change only `window_days` to 365.
8. Record hashes of every source and destination configuration document.

### Phase C — hostname and branding

1. Add and validate the `synthetic-awards.terianix.ai` custom hostname in the
   existing frontend delivery layer.
2. Create the required DNS CNAME/TXT records and complete managed TLS validation.
3. Add the origin to authentication redirect URIs and allowed frontend origins
   where required.
4. Deploy the logo asset and validate the absolute URL.
5. Verify that host-based tenant resolution returns Synthetics Inc.

### Phase D — users and hierarchy

1. Generate the deterministic Armenian name roster.
2. Build the manager hierarchy and role assignments.
3. Provision or reconcile all 400 disabled corpus accounts in Microsoft Entra.
4. Apply Entra department fields and manager relationships.
5. Resolve or create the enabled `David64 Terian` account and assign the Entra
   `AWard_Nomination_Admin` app role.
6. Insert or reconcile all 401 corresponding `dbo.Users` rows.
7. Validate one-to-one Entra/SQL UPN coverage, uniqueness, manager references,
   tenant membership, department values, account-enabled policy, and the
   requested UPN suffix.

### Phase E — scenario plan and nominations

1. Build hidden scenario truth and stable scenario IDs.
2. Allocate exactly 3,000 nominations and 60 fraud cases to each segment.
3. Give each fraud target two strictly earlier causal precursor rows.
4. Allocate 180 targets to active context and 120 to established context.
5. Create hard-negative cohorts before generating target rows.
6. Generate categories, amounts, descriptions, approvers, timestamps, statuses,
   and audit actors.
7. Insert nominations and their synthetic historical decision envelopes.
8. Commit only after all count, relationship, JSON, and date validations pass.

### Phase F — analytics and GNN

1. Run Graph Analytics and RF training normally for Synthetics Inc.; their
   output does not modify ground truth.
2. Run the GNN training job with candidate architectures from the cloned policy.
3. Confirm the job reports 180 fraud/8,820 legitimate rolling-train targets and
   60 fraud/2,940 legitimate final-holdout targets, allowing small differences
   only when explained by explicit eligibility rules.
4. Inspect candidate `metrics.json` files and the selected architecture.
5. Verify that the serving artifact references the synthetic generation run.
6. Score a new, separately generated post-holdout validation batch.
7. Run GNN explanation reproduction and confirm immutable score matching.

## 13. Acceptance criteria

### 13.1 Tenant and configuration

- Exactly one tenant matches the provided organization identifier.
- Tenant name, hostname, site URL, logo URL, and synthetic flag match this
  document.
- Allowed configuration matches Tenant 1 except for documented branding,
  identity, notification, payroll, and GNN-window differences.
- There is exactly one active Graph policy and one active GNN policy.
- No Tenant 1 policy identity or category identity is reused.

### 13.2 User and graph isolation

- Exactly 400 corpus users and one operational administrator exist.
- Every destination UPN ends in `@synthetics.terian-services.com`.
- All 401 SQL users have exactly one matching Microsoft Entra identity, and all
  401 Entra identities have exactly one matching SQL user.
- The 400 corpus Entra accounts are disabled and unlicensed.
- The David64 Terian Entra account is enabled.
- Every corpus user has a unique Armenian first-name/last-name combination.
- `David64 Terian` has the Entra `AWard_Nomination_Admin` app role and has no
  seeded nomination edges.
- Every `Users.Title` value belongs to the approved 18-department list.
- No nomination is a self-nomination.
- Every nominator, beneficiary, approver, and manager belongs to the destination
  tenant.
- There are no foreign or orphan user references.
- At least 350 users participate in the graph.

### 13.3 Corpus and labels

- Exactly 15,000 nominations exist inside the 365-day window ending immediately
  before the exclusive `2026-09-14` boundary.
- Exactly 14,700 are `LEGITIMATE` and 300 are `FRAUD`.
- Every chronological segment contains 3,000 nominations and 60 fraud labels.
- Every fraud target has exactly two strictly earlier legitimate precursors
  carrying the same stable scenario ID.
- The corpus contains exactly 180 `ACTIVE` and 120 `ESTABLISHED` fraud targets.
- Every training label source is `SYNTHETIC_GROUND_TRUTH`.
- No row claims a human reviewer for synthetic truth.
- No description or user-facing field exposes scenario or label markers.

### 13.4 Model validation

- GNN volume, user, temporal, positive-class, and negative-class gates pass.
- At least two graph candidates complete or produce an explicit failure reason.
- Candidate comparison uses the untouched S4 holdout.
- The MLP remains a non-serving admission baseline.
- The selected artifact, preprocessing, mappings, metrics, and hashes form one
  immutable versioned bundle.
- Synthetic performance is labeled synthetic everywhere it is displayed.

## 14. Operational verification queries

Always resolve the destination rather than hardcoding TenantId:

```sql
DECLARE @OrganizationId VARCHAR(36) = 'f74bff31-f42f-4461-a1dd-e1ae978c1abe';
DECLARE @TenantId INT = (
    SELECT TenantId
    FROM dbo.Tenants
    WHERE AzureAdTenantId = @OrganizationId
);

SELECT TenantId, TenantName, AzureAdTenantId, Domain, Site_URL,
       Company_Logo_URL, is_demo, is_synthetic
FROM dbo.Tenants
WHERE TenantId = @TenantId;

SELECT
    COUNT(*) AS TotalUsers,
    SUM(CASE WHEN userPrincipalName =
        'david64.terian@synthetics.terian-services.com' THEN 1 ELSE 0 END)
        AS TenantAdmins,
    SUM(CASE WHEN userPrincipalName <>
        'david64.terian@synthetics.terian-services.com' THEN 1 ELSE 0 END)
        AS CorpusUsers
FROM dbo.Users
WHERE TenantId = @TenantId
  AND userPrincipalName LIKE '%@synthetics.terian-services.com';

SELECT idr.TrainingDisposition,
       idr.TrainingDispositionSource,
       COUNT(*) AS LabelCount
FROM dbo.IntegrityDecisionResults idr
WHERE idr.TenantId = @TenantId
GROUP BY idr.TrainingDisposition, idr.TrainingDispositionSource;

SELECT COUNT(*) AS CrossTenantReferenceCount
FROM dbo.Nominations n
JOIN dbo.Users nom ON nom.UserId = n.NominatorId
JOIN dbo.Users ben ON ben.UserId = n.BeneficiaryId
JOIN dbo.Users app ON app.UserId = n.ApproverId
WHERE nom.TenantId = @TenantId
  AND (ben.TenantId <> @TenantId OR app.TenantId <> @TenantId);
```

Add schema guards to the validation tool so it reports a clear prerequisite
failure when `is_synthetic` or the provenance fields have not yet been deployed.

## 15. Reset and regeneration

Reset is permitted only because the complete tenant is synthetic. Use the
one-time `scripts/synthetic_tenant/reset_synthetics_inc_corpus.sql` runbook; a
permanent replacement mode is intentionally not part of the application or
seeder.

The script resolves the tenant from the exact organization identifier and then
verifies its name, domain, synthetic flag, 401-user count, deployed v2.0 corpus
hash, generation run ID, and exact 5,000-row ownership envelope. It fails closed if a
nomination is not manifest-owned, a cross-tenant reference exists, or a later
migration has introduced an unrecognized foreign-key child.

By default, `@CommitChanges = 0`: the script obtains locks, prints its affected
table inventory, performs the full operation and post-delete checks, and rolls
everything back. After reviewing that preview, set `@CommitChanges = 1` and run
the entire script again. The committed operation:

1. removes the v2.0 nominations, decisions, logs, Service Bus processing rows,
   graph edges, nomination embeddings, findings, graph flags, tenant GNN user
   embeddings, and corpus-related Graph change requests;
2. preserves all 401 SQL and Entra users, including `David64 Terian`;
3. preserves the tenant, hostname, categories, templates, roles, and active
   Graph/GNN scoring policies;
4. marks the RF, Graph, and GNN serving pointers unavailable until rebuilt; and
5. retains `IntegrityComponentStatus` temporal history as an audit trail.

Immutable blobs are not deleted. Invalidating their serving pointers prevents
them from being selected, while the next analytics run publishes new versioned
artifacts. After the committed reset, run the v3.0 seeder `--apply-corpus`
workflow. This path uses the provider-hosted SQL connection, requires the full
401-user roster, and deliberately skips Microsoft Graph because the customer
directory identities were preserved. Then run the analytics job.

## 16. Implementation boundary

This document approves the design, population, branding, and build sequence.
The v3.0 corpus and shared causal contract are implemented and validated
offline. Applying v3.0 requires the guarded SQL reset above followed by the
resumable `--apply-corpus` workflow. Do not manually delete individual rows or
run a corpus apply before the reset transaction commits.
