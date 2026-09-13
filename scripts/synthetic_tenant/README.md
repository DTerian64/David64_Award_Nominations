# Synthetics Inc. corpus generator

This package is the deterministic, read-only Phase-A generator for the
Synthetics Inc. GNN validation tenant. It creates the logical 400-user and
5,000-nomination plan in memory, validates all exact quotas, and prints a
reproducibility manifest. It does not mutate Microsoft Entra, SQL, Service Bus,
or model artifacts.

From the repository root:

```powershell
python -m scripts.synthetic_tenant.seed_synthetics_inc --dry-run
python -m scripts.synthetic_tenant.seed_synthetics_inc --validate --as-of 2026-09-12
python -m scripts.synthetic_tenant.seed_synthetics_inc --apply-configuration
python -m scripts.synthetic_tenant.seed_synthetics_inc --apply --manifest-out Output/synthetics-inc-manifest.json
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
