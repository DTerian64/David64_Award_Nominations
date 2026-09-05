# Integrity Analysis

The former **Model Analysis** workspace is now **Integrity Analysis**. It remains
available to administrators and Data Scientists under the existing authorization
policy. The effective user's tenant is used, including during impersonation.

## Nomination Analysis

Formerly **Independent Models**. Search nominations and inspect the existing
read-only engine evidence, explanations, pair history, and logs.

## User Analysis

1. Search by name, email, or user ID and select a user.
2. Filter their nominations by Nominator, Nominee, or Either; nomination date;
   engine with a concern; composite risk; and recorded HRBP outcome.
3. Inspect the summary and four-engine evidence for each nomination. Click the
   nomination number to open Nomination Analysis or **View logs** for its log drawer.

User search and nomination results are paginated. Separate **Nominations made**
and **Nominations received** summary cards distinguish the user's two roles.
Summary counts cover the entire filtered nomination population, not just the current page. Both participants and
the decision record must belong to the effective tenant. Changing impersonation
resets the workspace's local results.

An **engine concern** is an available engine reporting a nonempty finding, a
LOW-or-higher risk level, or a semantic flag/rejection. This includes signals below
HRBP routing thresholds. The engine filter selects nominations where that engine
raised such a concern; the risk filter always uses composite risk.

Summary categories intentionally overlap:

- **Human-confirmed issues**: explicit `CONFIRMED_CONCERN` or
  `CONFIRMED_SEMANTIC_CONCERN` outcomes.
- **Cleared — no concern**: `CLEARED_NO_CONCERN`.
- **Cleared — unsubstantiated**: `CLEARED_UNSUBSTANTIATED`.
- **Not for training**: explicit `EXCLUDED` disposition, including confirmed
  semantic concerns and unsubstantiated reviews. A missing disposition is not
  counted as an exclusion.
- **No inference recorded**: no matching `IntegrityDecisionResults` row. This is
  different from an existing decision with an unavailable engine.

Signals describe nominations involving a user, not proof of wrongdoing by that
user. Rejected nomination status alone never becomes a confirmed issue or a
training label. These screens do not modify workflow or training data.

Graph Analytics uses the highest relevant finding as its score. The inference
verdict identifies the biggest-contributing pattern and its count, then presents
the finding of that type with the maximum `finding_score`, using the same evidence
fields as Graph Pattern Findings. Scores are not summed. Full structured pattern evidence
remains in the persisted Graph result and nomination audit data. Historical
results containing repeated display strings are compacted when read, so no data
migration is required.

Canonical four-engine records keep explanations with their source engine: the RF
card contains RF findings and its SHAP-based **LLM explanation**, the Semantic card
contains the semantic finding description, and the Graph card contains the winning
pattern. The former shared findings strip and standalone LLM panel are shown only
for legacy records that do not have engine-specific evidence.

## Integrity Setup

Formerly **Model Setup**, with two sections:

- **Scoring & Routing** (formerly **Fraud / Integrity**): engine thresholds,
  routing configuration, and semantic pre-check settings.
- **Engine Status** (formerly **Decision Engines**): read-only operational
  status, model inspection, and the active Graph Analytics scoring policy.

These are naming changes only; settings, access controls, and APIs are unchanged.

## Deployment and verification

Deploy backend and frontend together. No new tables, migrations, backfill, or
engine changes are introduced. The database must already include revision 0054
(`IntegrityDecisionResults.TenantId`). Historical coverage depends on existing
decision records; this view does not reconstruct missing historical inference.

From `backend`:

```powershell
python -m pytest tests/test_user_analysis.py tests/test_data_scientist_access.py tests/test_hrbp_adjudication.py -q
```

From `frontend`:

```powershell
node --test tests/user-analysis.test.mjs tests/hrbp-review.test.mjs tests/graph-evidence.test.mjs
npm run build
```

The backend regression tests mock database calls. After sandbox deployment,
verify name/email/ID search, both role directions, engine/risk/outcome filters,
inclusive date boundaries, pagination, and links to analysis/logs against real
SQL Server data. Check a user from another tenant returns 404, and confirm that
semantic confirmations and unavailable engines display without fraud-label or
clean-result assumptions.
