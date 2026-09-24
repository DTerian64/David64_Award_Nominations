# Synthetics Inc. v5 — participation and description realism

Status: **implemented locally; Tenant 5 reset, reseed, migration, and deployed
Graph verification remain pending**

Baseline: [v4 specialist corpus](synthetics_inc_specialist_corpus_v4.md)

Scope: synthetic nomination generation, description quality, and Graph Analytics.
**No Tabular or GNN feature, fraud label, or routing change.**

## 1. Purpose and boundaries

The v4 corpus contains 400 synthetic users but only 360 nomination participants.
Its 40 quiet users are scattered across reporting teams, so they do not create
a Nomination Desert under the existing detector. Its background nomination
formula also repeats the same nominator–beneficiary pairs, inadvertently
teaching Tabular models that heavy pair repetition is normal.
Description generation has the same problem: the 15,000-nomination v4 corpus
has only **25 distinct descriptions**. Each of the five categories has five
descriptions, each repeated exactly 600 times. This is unsuitable as a
realistic semantic or Copy-Paste test corpus.

V5 retains the 400-user directory roster, separate administrator, 15,000
nominations, 365-day window, 300 fraud outcomes, six confirmed GNN pattern
families, and feature-matched legitimate controls. It changes *who
participates* so the graph contains two independently verifiable, legitimate
organizational phenomena:

1. **Nomination Desert:** all members of a reporting team have never nominated
   or received a nomination.
2. **Frequent nominator, seldom nominated:** an individual nominates several
   distinct people but is nominated at most once in the detection window.

Both are participation findings, not evidence of fraud. Neither may change a
nomination's route, create an HRBP review, populate `confirmed_patterns`, or
create a fraud training label. A desert has no nomination event at all, so it
cannot be a per-nomination Tabular feature. The individual finding is likewise
owned by Graph Analytics rather than Tabular in this design.

## 2. Corpus identity and approval fidelity

- Preserve the existing 400 synthetic Entra and SQL users and the separate
  operational administrator. No new user accounts or role assignments.
- Select four disjoint nine-report teams from different organizational areas.
  Keep each manager **and** their nine direct reports quiet: 40 intentionally
  nonparticipating users. This is a proposed allocation, not an explanation of
  the old hard-coded 360-user limit.
- The remaining 360 users may appear as nominators or beneficiaries, subject
  to the normal approval rule. The root executive may nominate but cannot be
  a beneficiary because no manager can approve an award for that user.
- For every generated nomination, set `ApproverId` to the **beneficiary's**
  manager, matching the live nomination API. A missing beneficiary manager is
  a generation error. Do not create a special self-approval detector or route.
- Keep the v4 fraud distribution and temporal specialist evidence intact. A
  designated quiet user must not enter any fraud precursor, fraud target,
  legitimate control, or ordinary background nomination.
- Bump the corpus generator version and SHA-256 identity. The currently
  deployed v4 corpus and its manifest remain immutable evidence; v5 requires
  a new manifest and a separately approved reset/reseed.

## 3. Background nomination pairing

Replace the modulo-based fixed pair mapping with reproducible, per-nomination
pseudorandom selection. Prefer same-department recipients, while retaining a
meaningful cross-department share. Proposed initial weight: **two-thirds
same department, one-third cross department**; make the weight explicit in
the generator and manifest, not an implicit consequence of user IDs.

Selection rules:

- Exclude self-nominations, the 40 quiet users, and beneficiaries without a
  manager. The administrator is never a participant.
- Use a stable seed keyed by nomination identity so adding one scenario does
  not reshuffle every unrelated background nomination.
- Allow plausible legitimate repeat collaborations. Do not cap pair frequency
  at a value that would make every higher count a fraud-only shortcut.
- Keep scenario precursor and target edges causally ordered. Do not overwrite
  their topology merely to achieve a department quota.
- Publish distributions by label and time segment for pair counts, department
  matches, incoming/outgoing counts, and distinct counterparties. Check that
  no one field or synthetic user cohort trivially predicts the fraud label.

The pairing weight and any legitimate-repeat mixture are corpus-design
parameters to approve **before** generation, not values tuned to obtain a
desired model admission result.

## 4. Category-grounded nomination descriptions

Generate a specific, plausible nomination story rather than selecting one of
five category-wide sentences. Use curated, category-specific banks of actions,
business contexts, and observable outcomes, combined only through compatible
story plans. Vary sentence structure, length, detail, and voice. The five
categories should remain semantically distinguishable without requiring every
description to repeat the literal category name or end with the same stock
sentence. Examples of distinct story domains are customer recovery and service
improvements; exceptional help outside normal duties; a tested process or
technical innovation; coaching and knowledge transfer; and cross-team delivery.
Each description must name its actual synthetic beneficiary and naturally
include that nomination's dollar amount. Do not include real-person identifiers
or invented claims of independent verification.

Use a deterministic, versioned text-generation seed keyed by stable nomination
identity and a story-plan ID, **not** `global_index % 5`. Background, fraud
scenario, precursor, and legitimate-control nominations all use the same text
generation system. A description may reflect the nominated contribution, but
must not contain a phrase, format, length, category token, or template family
that reveals `TrainingDisposition`, `confirmed_patterns`, or the intended
specialist. Reusing a story plan must not imply reusing its exact wording.

The design includes three distinct text profiles:

| Profile | Intended role |
|---|---|
| Individual stories | Default: substantively varied, category-fitting descriptions, with many unique combinations of context and outcome. |
| Benign recurring work | Some descriptions of recurring campaigns or shared efforts may be related or occasionally identical across nominations; repetition itself is not fraud. |
| Deliberate copy-paste cases | Small, explicitly tagged-for-validation exact and near-duplicate clusters across nominators and time; include legitimate controls as well as fraud cases, without making duplication a fraud-label shortcut. |

Exact-repeat clusters must describe the **same beneficiary and amount** on
every member row. Where necessary, only background nominations may have their
recipient and amount aligned to a benign repeat group's anchor; specialist
targets and precursors keep their causal topology. Fraud-linked repeat groups
may reuse the target's text on legitimate background nominations for that same
beneficiary, with amount aligned. This prevents a repeated description from
naming a different person or award than the stored nomination.

Proposed first-pass distribution: **at least 90% exact-unique descriptions
within each category**; **3–6% of nominations** belong to intentional
exact-reuse groups of two to eight; report near-paraphrase groups separately.
These are corpus-quality review targets, not model thresholds. A repeated
description must be an intentional group member, never an accidental collision
from modular indexing. No exact text may occur more than eight times without
an explicit design exception and review. Do not expand the existing six fraud
families or create a Copy-Paste fraud label merely to satisfy these targets.

Text similarity is a separate constraint from exact uniqueness. Graph
Analytics' `CopyPaste` detector currently joins nominations into a cluster
when embedding cosine similarity reaches its policy threshold (default
**0.92**) and reports clusters of at least three. Its connected-component
clustering can merge many ostensibly different templates through similarity
chains. The live description check separately compares a nominator's new
description with that nominator's prior descriptions (default threshold
**0.85**). Validate the generated corpus against the **actual active tenant
policies** and embedding model: intended repeat clusters should be detectable,
but generic category wording must not create giant cross-user clusters or
routine same-nominator review flags. Record any unavoidable benign alerts as
controls, not as fraud outcomes. Do not tune the text generator to force a
desired model PR-AUC or Graph finding count.

The current generator validation insists that the literal category name appear
in every description. Replace that assertion with category-intent checks and
reviewed samples; otherwise the text remains mechanically repetitive. Include
deliberately category-misaligned descriptions only in a separately specified
semantic-check test set, not silently in the standard v5 corpus. Before any
reseed, publish per-category exact-unique counts, largest repeat group,
length distribution, intended versus incidental near-duplicate clusters,
same-nominator duplicate rates, and repeated-text overlap across temporal
train/holdout folds. Inspect representative examples and alert cases manually.

## 5. Team-level Nomination Desert

Reuse the existing `Desert` detector and `dbo.GraphPatternFindings`. It groups
users by `ManagerId` and requires every direct report in a qualifying team to
be absent from **all-time** nomination history on both sides. It does not
count the manager as a team member; v5 nevertheless keeps the manager quiet
for an unambiguous business example. The existing minimum team size is three.

At the final v5 snapshot, the four designated teams should produce four
`Desert` findings, each with the correct member IDs and no supporting
nomination IDs. These findings remain analytics-only (`EnabledForRouting=0`).
Any additional desert teams should be reported and investigated rather than
silently accepted or removed to satisfy a target count.

## 6. Individual participation imbalance

Add an analytics-only Graph pattern with proposed internal key
`LowRecognitionNominator` and UI label **Frequent nominator, seldom
nominated**. It is not `HiddenCandidate`: that detector requires name mentions
in descriptions and no formal beneficiary nominations. It is also not
`Desert`: these users actively nominate others.

Proposed default eligibility, evaluated over the tenant's Graph detection
window using the same eligible nomination statuses as other rolling Graph
detectors:

| Measure | Proposed default |
|---|---:|
| Nominations made | at least 8 |
| Distinct beneficiaries | at least 4 |
| Nominations received | at most 1 |

The finding identifies the nominator as the affected user, stores the actual
made/received/distinct counts and supporting nomination IDs, and explains the
window used. It should not claim that the user deserves an award or that
another user committed fraud. Its Graph score is an **analytics-priority
score**, not fraud probability; its threshold and score weights belong in
`GraphScoringPatternParameters.ParametersJson`. Set
`EnabledForRouting=0`, and ensure nomination inference ignores this pattern.

Designate a small, seed-stable cohort of **12** non-desert users across at
least six departments. They may nominate in ordinary and scenario activity,
but beneficiary selection must respect the at-most-one-received bound. Do not
encode cohort membership in descriptions, categories, amounts, model inputs,
or training metadata. The generator should verify that these users actually
meet the detector's counts; naturally occurring additional findings should
be reported separately.

The proposed 8/4/1 cutoffs and 12-user cohort are review points, not existing
production policy. The detection window should be explicit in the tenant's
Graph policy; v5 validation should use the same window as the Graph job.

## 7. Graph policy and application changes

No new findings table is required. The current `GraphPatternFindings` schema
can store the new pattern and its evidence. Implementation after design
approval should:

1. Add `LowRecognitionNominator` detection to the weekly Graph Analytics job.
   Keep `Desert` as its existing all-time detector.
2. Add a versioned policy row with configurable eligibility and score
   parameters. Preserve published policies; create a new active version rather
   than editing historical active rows in place. Draft-policy behavior and
   display order must stay consistent with other Graph patterns. Enable the
   same analytics-only detector and default thresholds for **every tenant**;
   do not create a tenant-specific dormant variant.
3. Keep the new pattern disabled for routing in database policy, API
   validation, snapshot/inference filtering, and tests. It is a user-analysis
   finding, not a decisive nomination engine verdict.
4. Add its human-readable label and description to Graph Analytics, Graph
   policy setup, model-analysis filters, user analysis, and finding exports.
   Explain the made/received counts rather than presenting the score as a
   probability of fraud.
5. Do not alter Tabular feature columns, GNN pattern heads, or
   `TrainingDispositionMetadataJson` for either participation scenario.

## 8. Validation and release gate

Before any database write, the dry run must prove:

- 400 preserved corpus users plus one administrator, 15,000 nominations,
  14,700 legitimate outcomes, 300 fraud outcomes, and unchanged six-family
  specialist label quotas across temporal folds;
- exactly 40 designated quiet users in four complete reporting teams, none
  appearing as nominator or beneficiary, and no administrator participation;
- 360 nomination participants, including the root executive as a nominator
  but never as a beneficiary;
- at least 12 designated frequent nominators satisfy the published
  made/distinct/received thresholds, with incidental matches listed;
- every approver equals the beneficiary's manager and every beneficiary has
  a manager;
- realistic same-/cross-department and pair-frequency distributions, with
  no deterministic high-pair-count label shortcut;
- category-grounded description variety and approved exact-reuse rates, with
  no unplanned repeated text above the maximum group size;
- similarity-cluster and same-nominator duplicate diagnostics under the active
  Graph and semantic policies, including benign alerts and cross-fold overlap;
- no participation cohort identifier or Graph finding copied into GNN or
  Tabular training labels.

After an explicitly approved reset and v5 seed, run Graph Analytics and
verify the four intended `Desert` findings and the individual cohort findings
from persisted SQL data. Compare expected and actual affected user IDs and
counts. Only then retrain the existing Tabular and GNN models and inspect
their holdout metrics; model admission is **not** a corpus-generation gate.

No reset, migration, deployment, or reseed is authorized by this design
document alone.

## 9. Local implementation evidence (2026-09-24)

The fixed-seed dry run (`--seed 20260921 --as-of 2026-09-24`) validates
15,000 nominations, 300 fraud labels, all six unchanged specialist quotas,
four complete quiet teams, and the twelve designated frequent nominators.
Running the actual Graph detector functions on the generated logical records
produces four `Desert` and twelve `LowRecognitionNominator` findings. The root
executive is excluded from the latter because that user has no manager and is
ineligible to receive an award.

V4 background pairing used 360 distinct pairs, with a maximum of 33 repeats
per pair. V5 has **7,643 distinct background pairs**, maximum **8** repeats,
and a realized **63.02%** same-department share after exact-repeat groups are
aligned to their common beneficiary. This remains within the proposed
department-weighted mix, but is reported separately from the initial
two-thirds sampling weight.

V5 has **14,500 unique descriptions** overall (2,900 per category). Exactly
250 three-nomination reuse groups account for 750 rows: 15 fraud and 735
legitimate, both 5% of their respective classes. Every description names its
stored beneficiary and amount. A read-only, 1,000-row sample with the same
`all-MiniLM-L6-v2` model and default 0.92 threshold found eight non-exact
similar pairs, no cross-category pairs, and no sample cluster of three or
more. **This is a sample, not a substitute for the full Graph Analytics run
after reseeding.**
