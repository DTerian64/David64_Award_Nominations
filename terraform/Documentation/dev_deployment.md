# Dev Environment Deployment Guide

Complete step-by-step guide for deploying the `rg_award_nomination_dev` environment from scratch.

---

## Prerequisites

Before starting, ensure you have:

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5.0 installed
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) installed and logged in
- Access to the `awardnominationmodels` storage account (prod — holds tfstate)
- Your current public IP address ([whatismyip.com](https://whatismyip.com))

```powershell
# Verify Azure login
az account show

# Verify Terraform version
terraform version
```

---

## Pass 1 — Initial Infrastructure

### Step 1 — Fill in terraform.tfvars

Open `terraform/environments/dev/terraform.tfvars` and replace all `YOUR_*` placeholders:

| Variable | Description | Example |
|---|---|---|
| `my_ips` | Your current public IP | `["76.157.73.162"]` |
| `sql_admin_login` | SQL admin username | `sqladmin` |
| `sql_admin_password` | SQL admin password (upper + lower + number + symbol) | `P@ssw0rd123!` |
| `secrets.SQL-USER` | Same as sql_admin_login | `sqladmin` |
| `secrets.SQL-PASSWORD` | Same as sql_admin_password | `P@ssw0rd123!` |
| `secrets.GMAIL-APP-PASSWORD` | Gmail app password (not your Gmail password) | `xxxx xxxx xxxx xxxx` |
| `secrets.GMAIL-USER` | Gmail address used for sending | `you@gmail.com` |
| `secrets.FROM-EMAIL` | Sender email shown to recipients | `you@gmail.com` |
| `secrets.FROM-NAME` | Sender name shown to recipients | `Award Nominations` |
| `secrets.EMAIL-ACTION-SECRET-KEY` | Random secret for email tokens | `openssl rand -hex 32` |

Leave these as-is for Pass 1 — they are auto-wired or filled in Pass 2:
```hcl
swa_redirect_urls = []   # ← leave empty, fill after Pass 1
# AZURE-STORAGE-KEY and AZURE-OPENAI-KEY are auto-wired from module outputs
```

### Step 2 — Bootstrap tfstate Container (first time only)

Only needed if the `tfstate` container doesn't already exist in `awardnominationmodels`:

```powershell
cd terraform/scripts
bash bootstrap.sh
```

### Step 3 — Terraform Init

```powershell
cd terraform/environments/dev
terraform init
```

Expected output:
```
Initializing the backend...
Initializing modules...
Initializing provider plugins...
- Installing hashicorp/azurerm
- Installing hashicorp/azuread
Terraform has been successfully initialized!
```

### Step 4 — Terraform Plan

```powershell
terraform plan -out terraform.tfplan
```

Verify the plan output:
- `Plan: 74 to add, 0 to change, 0 to destroy`
- All resources prefixed/suffixed with `-dev`
- Resource group is `rg_award_nomination_dev`
- No `-` destroy lines

### Step 5 — Terraform Apply

```powershell
terraform apply "terraform.tfplan"
```

> **Note:** This takes 15–20 minutes. Container App Environments are the slowest resource (~10 min each). Do not interrupt.

---

## Pass 2 — Wire Outputs and Re-Apply

After Pass 1 completes, collect the generated values and update configuration.

### Step 6 — Collect Outputs

```powershell
# ACA managed identity IDs — needed for Key Vault access
terraform output aca_east_principal_id
terraform output aca_west_principal_id

# SWA deployment token — needed for GitHub Actions
terraform output -raw swa_deployment_token

# ACR login server
terraform output acr_login_server

# Public URLs
terraform output app_url
terraform output frontend_url
terraform output grafana_url
```

### Step 7 — Update terraform.tfvars

Open `terraform/environments/dev/terraform.tfvars` and update:

```hcl
# Add ACA principal IDs from Step 7 output
# In main.tf, update the key_vault module:
#   aca_principal_ids = ["<aca_east_principal_id>", "<aca_west_principal_id>"]

# Add SWA URL for Azure AD app registration redirect URI
swa_redirect_urls = ["https://<your-swa-hostname>.azurestaticapps.net/"]
```

Update `main.tf` key_vault module block with the ACA principal IDs:

```hcl
module "key_vault" {
  ...
  aca_principal_ids = [
    "<aca_east_principal_id>",
    "<aca_west_principal_id>"
  ]
}
```

### Step 8 — Re-Plan and Re-Apply

```powershell
terraform plan -out terraform.plan
terraform apply "terraform.plan"
```

This apply is much faster — only the Key Vault access policies and app registration redirect URIs change.

---

## Post-Deploy Steps

### Step 9 — Approve AFD Private Link Connections

The Container App Environments use Private Link to connect to Front Door. These must be manually approved in the portal.

```
Azure Portal
  → rg_award_nomination_dev
  → cae-award-eastus-dev
  → Networking → Private endpoint connections
  → Select pending connection → Approve

Repeat for:
  → cae-award-westus-dev
  → Networking → Private endpoint connections → Approve
```

> **Note:** Until both connections are approved, Front Door returns `502 Bad Gateway`.

### Step 10 — Update GitHub Secrets

```powershell
# Get the SWA deployment token
terraform output -raw swa_deployment_token

# Update GitHub secret (requires GitHub CLI)
gh secret set SWA_TOKEN_DEV \
  --repo YOUR_ORG/David64_Award_Nominations \
  --body "$(terraform output -raw swa_deployment_token)"
```

Or manually in GitHub:
```
GitHub repo → Settings → Secrets and variables → Actions
  → Update SWA_TOKEN_DEV with value from: terraform output -raw swa_deployment_token
```

### Step 11 — Create Dev Branch in GitHub

```powershell
git checkout -b dev
git push origin dev
```

### Step 12 — Update GitHub Actions Workflows

Update your CI/CD YAML files to deploy to dev resources when pushing to the `dev` branch:

**Backend workflow (deploy-backend.yml):**
```yaml
env:
  APP_NAME_EAST: ${{ github.ref == 'refs/heads/main' && 'award-api-eastus' || 'award-api-eastus-dev' }}
  APP_NAME_WEST: ${{ github.ref == 'refs/heads/main' && 'award-api-westus' || 'award-api-westus-dev' }}
  ACR_NAME:      ${{ github.ref == 'refs/heads/main' && 'acrawardnomination' || 'acrawardnominationdev' }}
```

**Frontend workflow (deploy-frontend.yml):**
```yaml
- name: Deploy to prod SWA
  if: github.ref == 'refs/heads/main'
  uses: Azure/static-web-apps-deploy@v1
  with:
    azure_static_web_apps_api_token: ${{ secrets.SWA_TOKEN_PROD }}

- name: Deploy to dev SWA
  if: github.ref == 'refs/heads/dev'
  uses: Azure/static-web-apps-deploy@v1
  with:
    azure_static_web_apps_api_token: ${{ secrets.SWA_TOKEN_DEV }}
```

### Step 13 — Trigger First Dev Deployment

```powershell
git checkout dev
git commit --allow-empty -m "trigger dev deploy"
git push origin dev
```

Watch the GitHub Actions workflow run. On success the dev Container Apps will be updated with the real application image.

---

## Verification Checklist

After all steps are complete, verify:

```
[ ] Portal → rg_award_nomination_dev shows all resources
[ ] terraform output app_url returns 200 (may show placeholder until Step 14)
[ ] terraform output frontend_url loads the React app
[ ] terraform output grafana_url loads Grafana dashboard
[ ] GitHub Actions → dev branch workflow completes successfully
[ ] Container Apps show the application image (not the placeholder)
[ ] Key Vault shows all secrets populated
[ ] AFD Private Link connections show "Approved"
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| AFD returns `502` | Private Link not approved | Complete Step 9 |
| Container App shows placeholder image | GitHub Actions not triggered | Complete Step 13 |
| Key Vault access denied | ACA principal IDs not added | Complete Steps 7–8 |
| App can't read secrets | `aca_principal_ids` empty | Update main.tf and re-apply |
| SWA shows old prod app | Wrong `SWA_TOKEN_DEV` | Re-run Step 10 |
| `terraform apply` fails on OpenAI | Quota not available | Request gpt-4.1 GlobalStandard quota in East US via portal |

---

## Useful Commands

```powershell
# Show all outputs
terraform output

# Show specific sensitive output
terraform output -raw swa_deployment_token

# Check current state
terraform show

# Destroy dev environment (careful!)
terraform destroy

# Target a single resource
terraform apply -target=module.key_vault

# Check what changed since last apply
terraform plan
```

---

## Cost Estimate (Dev)

| Resource | SKU | Est. Monthly |
|---|---|---|
| SQL Database | GP_S_Gen5_2 Serverless | ~$15 (scale to zero) |
| Container Apps | 0 min replicas | ~$0 (scale to zero) |
| Storage | Standard_LRS | ~$1 |
| OpenAI | S0 10K TPM | ~$0 (pay per use) |
| Front Door | Standard | ~$35 |
| Static Web App | Free | $0 |
| Log Analytics | PerGB2018 | ~$2 |
| Grafana | Standard | ~$20 |
| Networking | VNets + Private Endpoints | ~$15 |
| **Total** | | **~$88/month** |

> Front Door is the largest cost driver. If dev is only used occasionally, consider destroying and re-creating with `terraform destroy` / `terraform apply` to save costs.
