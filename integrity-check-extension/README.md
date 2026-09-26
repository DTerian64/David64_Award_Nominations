# Integrity Check Extension

Asynchronous, non-routing work that extends a completed integrity decision.
The first extension is GNN score reproduction, followed by GNNExplainer in E3.

The worker validates
and claims `gnn.explanation.requested`, loads only immutable versioned artifacts,
and proves that both serving and reconstructed scores reproduce the stored GNN
probability. Actual GNNExplainer attribution execution remains unimplemented;
eligible automatic requests therefore record `FAILED` with
`EXPLANATION_ENGINE_NOT_DEPLOYED` after successful score reproduction.
The former tenant enable flag is no longer used by the producer or worker.

Run tests from this directory with:

```powershell
python -m pytest tests -v
```

## Sandbox deployment order

1. Apply the sandbox Terraform changes to create the subscription, identity,
   permissions, and Container App.
2. Set the sandbox GitHub Actions variable
   `CONTAINER_APP_INTEGRITY_CHECK_EXTENSION` to
   `award-integrity-ext-sandbox`.
3. Run **Deploy Integrity Check Extension**.

Deploy the producer, frontend, and extension changes together for consistent
lifecycle reporting. Attribution will not be available until E3 replaces the
`EXPLANATION_ENGINE_NOT_DEPLOYED` boundary; these requests cannot provide it yet.
