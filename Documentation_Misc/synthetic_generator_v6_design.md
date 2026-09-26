# Synthetic Generator v6: configurable generation and independent audit

## Boundary

Generate an immutable experiment from a versioned JSON configuration, a seed and
an exclusive `as_of` date. Labels come from explicit scenario construction,
never from Graph findings, RF scores or a scenario-to-pattern back-mapping.
Auditing reports deficiencies; it never changes labels, relaxes detector policy
or repeatedly samples until a model wins. A useful corpus cannot guarantee that
every GNN head will be admitted.

The initial profile preserves the 400 disabled corpus identities and separate
operational administrator. Only corpus identities participate in nominations.
It preserves the four quiet teams and twelve frequent-but-rarely-recognized
users. SQL application remains a manually initiated Tenant 5 operation; offline
experiments can change population size without modifying SQL or Entra.

## Engine windows

Migration 0069 applies the following Graph settings to **all tenants**, preserving
their unrelated configuration and their separate GNN/Tabular windows:

```json
{
  "graph_pattern": {
    "detection_window_days": 180,
    "detector_windows": {
      "Ring": 60,
      "BipartiteDenseBlock": 180,
      "TemporalBurst": 180,
      "SuperNominator": 180,
      "SuperBeneficiary": 180,
      "CopyPasteFraud": 180,
      "HiddenCandidate": 180,
      "LowRecognitionNominator": 180
    }
  }
}
```

The top-level value is a fallback for missing entries, not an additional filter
applied after the detector window. The job loads the longest required history
once and gives each detector its own interval. Nomination Desert remains all-time
analytics. `CopyPasteFraud` is the public configuration name; the existing internal
detector name is `CopyPaste`.

Weekly publication records the effective windows in the snapshot and diagnostics.
Live candidate evaluation uses those published windows and ages history relative
to the candidate timestamp. Changing configuration does not rewrite a previously
published snapshot. Setup and Engine Status show the per-pattern configured and
serving values, with pending changes distinguished.

## Structure and configuration

- `configuration.py`: validation and canonical configuration hash.
- `profiles/v6-balanced.json`: editable initial experiment profile.
- `generator_v6.py`: deterministic identities, background, episodes and text.
- `audit_v6.py`: production detector/feature audit and bundle publication.
- `seed_synthetics_inc.py`: preview, read-only policy export and explicit application.

The initial profile requests 15,000 nominations over 365 days, with 300 fraud
targets (2%) and 14,700 legitimate events. Its explicit label allocation is:

| Family | Fraud episodes | Targets per episode | Fraud labels |
| --- | ---: | ---: | ---: |
| RING | 25 | 2 | 50 |
| RECIPROCAL | 25 | 2 | 50 |
| TEMPORAL_BURST | 5 | 10 | 50 |
| SUPER_NOMINATOR | 5 | 10 | 50 |
| SUPER_BENEFICIARY | 5 | 10 | 50 |
| BIPARTITE_DENSE_BLOCK | 10 | 5 | 50 |

Episodes, target labels and detector findings are different units. One episode
can generate multiple findings; older episodes can be outside the current engine
window. Each family also has legitimate controls, with precursor history before
its targets. Five temporal segments spread labels and controls across training
and evaluation periods. Actor selection is not split into fraud/legitimate pools.

Background nominations use persistent working relationships, a configurable
department preference, unequal but overlapping activity, and weekday weighting.
This replaces uniform all-to-all interaction, without outlawing normal repeated
pairs, reciprocal recognition or incidental cycles. Dense project communities
have shared working-partner rosters. Episode volume, duration, target count,
ring size and dense-block dimensions are configuration inputs.

Descriptions use category-specific variations, the beneficiary's name and the
award amount. The initial profile requests 50 exact-reuse groups of three: forty
legitimate groups and ten overlays on independently labelled fraud episodes.
Text reuse changes neither topology nor ground truth. Amount distributions
overlap between classes and remain within category bounds. Approvers are the
beneficiary's manager. No beneficiary approves their own award.

These are provisional synthetic assumptions, not measured real-tenant prevalence.
In particular, 15,000 awards for 400 users is substantial activity. Future tenant
profiles should set counts, affinity, amounts and time distribution from approved
real summaries rather than treating this profile as universal bootstrap truth.

## Independent audit

Use the existing Graph detectors under their intended windows and policy, the
Tabular feature builder and shared GNN causal-context builder. Semantic auditing
uses the cached production `all-MiniLM-L6-v2` model, not fabricated embeddings.
It does not download models, train scorers, write embedding caches or contact SQL.

The report separates requested episodes, in-scope planted cases detected, missed
cases, out-of-scope cases, detected legitimate controls and incidental findings.
Reciprocal has no standalone Graph detector; its audit checks reverse-pair causal
features. Participation cohorts and description-reuse groups have separate
coverage reports. Labels are counted by category, department, calendar period and
fold and individual nominator/beneficiary. Feature distributions, train-fitted
actor-rate memory probes and chronological single-feature shortcut probes
make category/time shortcuts visible without changing ground truth.

Acceptance requires a complete semantic audit, a supplied active Graph policy,
and no reported acceptance warnings. Without either semantic evaluation or an
actual policy the report is explicitly `PARTIAL`, never an application approval.
The profile's 0.8 episode-recall target and 1,000 incidental-ring budget are editable
audit criteria, not production detector thresholds or model admission thresholds.

Publish together into a fresh directory, without overwriting an experiment:

1. `configuration.json`
2. `manifest.json`: seed, exclusive date, versions and configuration/corpus hashes.
3. `audit-report.json`: effective windows, supplied policy hash and measured results.
4. `corpus.json`: exact generated identities and nominations.

## Category Fraud Rate correction

`category-fraud-rate-forward-oof-v1` replaces leave-one-out target encoding.
Training rows are divided into five chronological blocks without splitting
simultaneous timestamps. Each block's rates use only events and outcomes already
known strictly before the block starts. Human labels use their recorded review
time; synthetic labels use their recorded availability time.

The first block uses a fixed 0.02 cold-start prior independent of its labels.
Subsequent category rates are smoothed toward the preceding history's global
rate with 20 pseudo-observations. Holdout encoding is fitted from training labels
known before holdout starts. Neither current-row nor future/holdout outcomes
contribute to an input. The serving refit also trains on forward-only encodings,
and stores its final known-label lookup for future inference.

RF and Tabular MLP use the same prepared holdout and retain held-out permutation
importance using PR-AUC (`average_precision`), with repeat variation reported.
Impurity importance remains a separate RF-only chart; it is not artificially
rescaled to make category importance smaller. Candidate metrics and preprocessing
artifacts record the encoding contract, prior, smoothing and fold evidence.
Existing published models/charts are unchanged until retraining and publication.

## Manual workflow

Deploy migration 0069 and the matching core/job/worker/backend/frontend changes.
No corpus reset is needed just to publish the new Graph windows. Retrain Tabular
models to obtain corrected category encoding and new permutation importance.

Generate a read-only preview first:

```powershell
python -m scripts.synthetic_tenant.seed_synthetics_inc --config scripts/synthetic_tenant/profiles/v6-balanced.json --seed 20260926 --as-of 2026-09-27 --audit --bundle-out Output/synthetics-inc-v6-preview
```

Export the active Tenant 5 policy after migration, using existing SQL credentials
(this export reads configuration; it does not modify it):

```powershell
python -m scripts.synthetic_tenant.seed_synthetics_inc --export-graph-policy Output/tenant5-graph-policy-v6.json
```

Run the complete audit against that exact policy:

```powershell
python -m scripts.synthetic_tenant.seed_synthetics_inc --config scripts/synthetic_tenant/profiles/v6-balanced.json --seed 20260926 --as-of 2026-09-27 --audit --semantic-audit --graph-policy Output/tenant5-graph-policy-v6.json --bundle-out Output/synthetics-inc-v6-policy-audit
```

Review misses, controls, incidental findings and shortcut probes before accepting
the experiment. The initial local full-semantic audit is not accepted: it lacks
the actual tenant policy and reports two missed in-window dense-block episodes.
Do not reseed from it yet. Refinements must change documented configuration or
scenario construction, then produce a new immutable bundle; never relabel misses.

Only after approval, manually preview/commit the existing Tenant 5 reset script,
then use `--apply-corpus` with the same complete-audit arguments and a fresh bundle
directory. Application requires acceptance, the preserved 400-person roster and
an unchanged active SQL policy. There is no automatic reset or Entra provisioning.
The no-configuration v5 path remains available for historical replay, not as a
hidden mapping or a shadow version of v6.
