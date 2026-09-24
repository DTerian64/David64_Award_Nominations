# Synthetics Inc. corpus generator

This package generates and provisions the deterministic Synthetics Inc. GNN
validation tenant. Its default and `--validate` modes create the logical
400-user and 15,000-nomination plan in memory, validate all exact quotas, and
print a reproducibility manifest without mutating Microsoft Entra, SQL, Service
Bus, or model artifacts.

Generator v5.0 creates 50 directly labelled fraud targets for each of the six
GNN specialist families: ring, reciprocal, temporal burst, super nominator,
super beneficiary, and bipartite dense block. Every fraud target persists
exactly one matching `confirmed_patterns` value. Legitimate rows persist an
empty array. The training loader does not infer or translate labels from a
scenario name.

Each behavior has its own causal topology rather than a shared shortcut.
Feature-matched legitimate controls are distributed across the same five
temporal segments. This supplies every specialist with 30 training positives,
10 positives in each of two selection folds, and 10 positives in the final
holdout. V5 also keeps four complete reporting teams quiet, designates twelve
frequent nominators who are seldom nominated, diversifies ordinary pairs, and
generates category-specific stories that name the actual beneficiary and
award amount. Each category has 2,900 distinct texts among 3,000 nominations,
including intentional exact-reuse groups whose beneficiary and amount agree.
The
default inclusive 365-day window ends on September 23, 2026, using the
exclusive `--as-of 2026-09-24` boundary.

From the repository root:

```powershell
python -m scripts.synthetic_tenant.seed_synthetics_inc --dry-run
python -m scripts.synthetic_tenant.seed_synthetics_inc --validate --seed 20260921 --as-of 2026-09-24
python -m scripts.synthetic_tenant.audit_descriptions --seed 20260921 --as-of 2026-09-24
python -m scripts.synthetic_tenant.seed_synthetics_inc --apply-configuration
python -m scripts.synthetic_tenant.seed_synthetics_inc --apply-corpus --seed 20260921 --as-of 2026-09-24 --manifest-out Output/synthetics-inc-v5-manifest.json
python -m scripts.synthetic_tenant.seed_synthetics_inc --apply --seed 20260921 --as-of 2026-09-24 --manifest-out Output/synthetics-inc-v5-manifest.json
```

The optional description audit uses locally cached `all-MiniLM-L6-v2` weights
and reports full-corpus exact repetition plus embedding-similar clusters in a
representative sample at the default 0.92 Graph threshold. It is read-only.
`--sample-step 1` checks all 15,000 texts but can be very slow on a local CPU;
the deployed Graph job remains the definitive full-corpus check. The SQL
seeder itself has no model download or embedding dependency.

The seed and `as-of` date are part of the corpus identity. The same two inputs
must produce the same SHA-256 hash. Persistence is isolated behind the explicit
apply modes and the fail-closed preflight described in the design document.
The existing directory roster has its own fixed seed, `20260912`; changing the
v5 corpus seed never regenerates or replaces those 400 user identities.

`--apply-configuration` is the Phase-B boundary. It requires the normal
`SQL_SERVER`, `SQL_DATABASE`, `SQL_USER`, and `SQL_PASSWORD` environment
variables and migration 0060. It creates or reconciles only tenant
configuration, categories, disabled email templates, and active Graph/GNN
policies. It does not create Entra or SQL users and does not load nominations.

`--apply` performs the resumable full population path. In addition to SQL
settings, configure `SYNTHETICS_AAD_TENANT_ID` and
`SYNTHETICS_AWARD_SERVICE_PRINCIPAL_ID`. Microsoft Graph authentication uses
`SYNTHETICS_GRAPH_AUTH=azure-cli` or `client-secret`. Azure CLI is selected
automatically when client credentials are absent; first run
`az login --tenant f74bff31-f42f-4461-a1dd-e1ae978c1abe --allow-no-subscriptions`.
For unattended execution, select `client-secret` and supply
`SYNTHETICS_GRAPH_CLIENT_ID` plus `SYNTHETICS_GRAPH_CLIENT_SECRET` instead.
If the administrator does not already exist, also supply
`SYNTHETICS_ADMIN_INITIAL_PASSWORD`; it is validated before the first corpus
identity is created and is never printed or written to the manifest. The
command verifies the custom UPN domain,
reconciles 400 disabled/unlicensed Entra users and the enabled administrator,
sets managers, assigns the administrator role, reconciles all 401 SQL users,
and atomically inserts the 15,000 nominations and their synthetic decision
envelopes. It never publishes Service Bus messages or calls an LLM.
The required manifest file retains the 401 UPN/Entra-object-ID/SQL-UserId
mappings; that detailed map is written to disk but omitted from console output.

`--apply-corpus` is the repeat-corpus path for an already provisioned customer
directory. It uses only the provider-hosted SQL connection, requires the exact
401-user SQL roster to exist, and does not request a customer-tenant Microsoft
Graph token. It validates the existing tenant identity, active Graph policy,
active GNN policy, and 365-day GNN training window without cloning or replacing
configuration from Tenant 1. Deploy migration 0066 and the corresponding
Graph Analytics/API/frontend changes before running Graph Analytics to inspect
the new participation finding. Its manifest records that the Entra directory and
tenant configuration were preserved rather than reconciled and retains the
complete logical-to-SQL identity map.

Do not run `--apply` to replace the currently deployed corpus. Stable
nomination identities intentionally cause the apply preflight to reject changed
generation metadata instead of silently rewriting history. First run
`reset_synthetics_inc_corpus.sql` as a rollback preview, review its inventory,
and rerun it with `@CommitChanges = 1`. The script preserves the tenant,
configuration, policies, all 401 SQL/Entra users, and the administrator. It
validates that TenantId 5 is the expected synthetic tenant, then removes every
TenantId 5 nomination, decision, and corpus-derived record, including later
test nominations. It then invalidates old serving pointers. After the committed
reset, run the v5.0
`--apply-corpus` command above. Keep the fixed seed and `as-of` date and write to
the new v5 manifest path so the deployed v4.0 manifest remains available as
reset provenance. No replacement-specific Python mode is required.
