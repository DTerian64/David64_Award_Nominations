# Deployment and DevOps Architecture

## Deployment Summary

The application is deployed to Azure using Terraform for infrastructure and GitHub Actions for application image/build deployment.

Primary Azure services:

- Azure Static Web Apps.
- Azure Front Door Standard with WAF.
- Azure Container Apps and Container Apps Jobs.
- Azure SQL.
- Azure Service Bus.
- Azure Container Registry.
- Azure Storage.
- Azure Key Vault.
- Azure OpenAI.
- Log Analytics and Application Insights.
- Logic App for HRBP SLA checks.

## Repository Deployment Assets

| Path | Purpose |
| --- | --- |
| `terraform/environments/sandbox` | Sandbox Terraform environment wiring. |
| `terraform/environments/dev` | Dev Terraform environment wiring. |
| `terraform/environments/prod` | Prod Terraform environment wiring. |
| `terraform/modules` | Reusable infrastructure modules. |
| `Deployment` | Bicep-based deployment resources and parameters. |
| `.github/workflows` | CI/CD workflows. |
| `backend/Deployment` | Legacy/manual backend deployment scripts and GitHub setup notes. |

## Infrastructure Modules

| Module | Responsibility |
| --- | --- |
| `app-registrations` | Entra app registrations and app roles. |
| `networking` | VNets, subnets, private DNS zones, peering. |
| `sql` | SQL Server, database, firewall/private endpoint patterns. |
| `container-registry` | ACR. |
| `storage` | Storage account and containers. |
| `key-vault` | Secrets and Key Vault access. |
| `openai` | Azure OpenAI resource and deployment. |
| `log-analytics` | Workspaces. |
| `application-insights` | App Insights resources. |
| `container-apps` | Primary and secondary backend Container Apps. |
| `auxiliary-container-app` | Auxiliary worker app. |
| `payroll-broker` | Payroll broker Container App. |
| `fraud-analytics-job` | Scheduled Container Apps Job. |
| `service-bus` | Namespace, topic, subscriptions, filters, RBAC. |
| `front-door` | Front Door, WAF, origins, routes, custom domains, CORS rule set. |
| `static-web-app` | Frontend hosting and build config. |
| `grafana` | Dashboard resources where used. |

## Runtime Services

| Service | Deployment target | Scaling |
| --- | --- | --- |
| Frontend | Azure Static Web Apps | Static hosting. |
| Backend API primary/secondary | Azure Container Apps | Configured min/max replicas, health probes. |
| Integrity Check | Azure Container Apps | KEDA Service Bus scale. |
| Auxiliary Worker | Azure Container Apps | KEDA Service Bus scale. |
| Payroll Broker | Azure Container Apps | Minimum 1 replica for webhook availability, KEDA worker inside process. |
| Fraud Analytics Job | Container Apps Job | Weekly cron and manual start. |
| HRBP SLA | Logic App | Weekday recurrence. |

## GitHub Actions

| Workflow | Purpose |
| --- | --- |
| `azure-static-web-apps.yml` | Builds frontend and deploys Static Web App. |
| `azure-backend_ACA_deployment.yaml` | Builds and deploys backend API image to primary and secondary Container Apps. |
| `azure-auxiliary_ACA_deployment.yaml` | Builds and deploys auxiliary worker. |
| `deploy-integrity-check.yml` | Builds and deploys integrity-check worker. |
| `deploy-payroll-broker.yaml` | Builds and deploys payroll broker. |
| `azure-fraud-analytics-job_deployment.yaml` | Builds and deploys fraud analytics Container Apps Job and can trigger validation run. |
| `train-fraud-model.yml` | Legacy/manual fraud model training and artifact upload. |
| `kusto_alert-monitoring.yml` | Runs alert-monitoring KQL workflow. |
| `kusto_daily-health-report.yml` | Generates daily health report. |
| `kusto_post-deployment-validation.yml` | Runs post-deployment validation queries. |
| `sync-to-devops.yml` | Syncs docs to Azure DevOps wiki. |

## Environment Strategy

Recommended environment tiers:

| Environment | Purpose |
| --- | --- |
| Local | Developer debugging using local frontend/backend and optional `.env`. |
| Sandbox | Demo, investor/customer validation, synthetic data, integration experimentation. |
| Dev | Active engineering validation. |
| Test/UAT | Controlled tenant validation and release candidate testing. |
| Prod | Customer production tenants. |

## Release Flow

Recommended release path:

1. Code change merged to main or release branch.
2. Unit and lint checks run.
3. Docker images build for changed services.
4. Images push to ACR with stable and SHA tags.
5. Container App image updates through Azure CLI.
6. Static frontend deploys through SWA CLI.
7. Post-deployment KQL validation runs.
8. Smoke tests validate `/health`, login, nomination create, event processing, analytics.
9. Rollback uses previous ACR image tag or Container App revision.

## Terraform and Image Ownership

Terraform provisions Container Apps with placeholder images. GitHub Actions owns image updates. Terraform `ignore_changes` prevents later applies from resetting images to placeholders.

This split is intentional:

- Terraform owns infrastructure shape, identity, secrets, networking, and scaling.
- CI/CD owns application images and deploy cadence.

## Rollback Strategy

Rollback options:

- Reapply previous ACR image tag with `az containerapp update`.
- Use previous Static Web App deployment if supported by SWA environment history.
- Re-run prior workflow with a known SHA tag.
- Roll back Terraform changes with a prior plan or module version.
- For database changes, prefer forward-fix migrations; design destructive rollback scripts only when explicitly tested.

## Backup and Restore

Recommended:

- Azure SQL automated backups and point-in-time restore.
- Blob Storage soft delete/versioning for model artifacts, certificates, and exports.
- Key Vault soft delete and purge protection.
- Terraform state backup and locking.
- Export tenant config snapshots before major onboarding/config changes.

## Cost Controls

Cost levers:

- Container App min replicas by environment.
- Fraud analytics job schedule and resource size.
- Service Bus SKU Standard vs Premium.
- SQL serverless auto-pause for sandbox/dev.
- Front Door and WAF traffic.
- Log Analytics retention and ingestion volume.
- Azure OpenAI usage for agents and explanation generation.

