# Integrity Check Extension

Asynchronous, non-routing work that extends a completed integrity decision.
The first extension is GNN score reproduction, followed by GNNExplainer in E3.

E1/E2 deliberately leave explanation generation disabled. The worker validates
and claims `gnn.explanation.requested`, loads only immutable versioned artifacts,
and proves that both serving and reconstructed scores reproduce the stored GNN
probability before later phases are allowed to explain it.

Run tests from this directory with:

```powershell
python -m pytest tests -v
```

## Sandbox deployment order

1. Keep every tenant's `dbo.GNNScoringPolicies.ExplanationEnabled` set to `0`.
2. Apply the sandbox Terraform changes to create the subscription, identity,
   permissions, and Container App.
3. Set the sandbox GitHub Actions variable
   `CONTAINER_APP_INTEGRITY_CHECK_EXTENSION` to
   `award-integrity-ext-sandbox`.
4. Run **Deploy Integrity Check Extension**.

Do not enable tenant requests until E3 replaces the intentional
`EXPLANATION_ENGINE_NOT_DEPLOYED` fail-closed boundary.
