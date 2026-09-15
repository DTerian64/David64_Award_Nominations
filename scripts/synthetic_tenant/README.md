# Synthetics Inc. corpus generator

This package generates and provisions the deterministic Synthetics Inc. GNN
validation tenant. Its default and `--validate` modes create the logical
400-user and 5,000-nomination plan in memory, validate all exact quotas, and
print a reproducibility manifest without mutating Microsoft Entra, SQL, Service
Bus, or model artifacts.

Generator v2.0 gives each of the 100 fraud targets exactly two earlier causal
precursor nominations. Sixty targets exercise topology actively forming after
a weekly snapshot; forty exercise relationships already established in an
earlier graph snapshot.

From the repository root:

```powershell
python -m scripts.synthetic_tenant.seed_synthetics_inc --dry-run
python -m scripts.synthetic_tenant.seed_synthetics_inc --validate --as-of 2026-09-12
python -m scripts.synthetic_tenant.seed_synthetics_inc --apply-configuration
python -m scripts.synthetic_tenant.seed_synthetics_inc --apply-corpus --seed 20260912 --as-of 2026-09-12 --manifest-out Output/synthetics-inc-v2-manifest.json
python -m scripts.synthetic_tenant.seed_synthetics_inc --apply --seed 20260912 --as-of 2026-09-12 --manifest-out Output/synthetics-inc-v2-manifest.json
```

The seed and `as-of` date are part of the corpus identity. The same two inputs
must produce the same SHA-256 hash. Persistence is isolated behind the explicit
apply modes and the fail-closed preflight described in the design document.

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
and atomically inserts the 5,000 nominations and their synthetic decision
envelopes. It never publishes Service Bus messages or calls an LLM.
The required manifest file retains the 401 UPN/Entra-object-ID/SQL-UserId
mappings; that detailed map is written to disk but omitted from console output.

`--apply-corpus` is the repeat-corpus path for an already provisioned customer
directory. It uses only the provider-hosted SQL connection, requires the exact
401-user SQL roster to exist, and does not request a customer-tenant Microsoft
Graph token. Its manifest records that the Entra directory was preserved rather
than reconciled and retains the complete logical-to-SQL identity map.

Do not run `--apply` to replace the currently deployed v1.1 corpus. Stable
nomination identities intentionally cause the apply preflight to reject changed
generation metadata instead of silently rewriting history. First run
`reset_synthetics_inc_corpus.sql` as a rollback preview, review its inventory,
and rerun it with `@CommitChanges = 1`. The script preserves the tenant,
configuration, policies, all 401 SQL/Entra users, and the administrator. It
removes only the manifest-owned v1.1 nomination corpus and corpus-derived data,
then invalidates old serving pointers. After the committed reset, run the v2.0
`--apply-corpus` command above. Keep the fixed seed and `as-of` date and write to
the new v2 manifest path so the deployed v1.1 manifest remains available as
reset provenance. No replacement-specific Python mode is required.
