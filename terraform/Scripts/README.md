# Award Nomination — Private Networking
## Terraform + Azure CLI Setup Guide

### What this builds

```
Internet
    │
Azure Front Door (WAF — public, unchanged)
    │  Private Link
    ▼
vnet-award-eastus (10.0.0.0/16)          vnet-award-westus (10.1.0.0/16)
  ├── subnet-aca-eastus (10.0.1.0/24)  ◄─peering─► subnet-aca-westus (10.1.1.0/24)
  └── subnet-privatelinks (10.0.2.0/24)
        ├── pe-sql-eastus
        ├── pe-blob-eastus
        ├── pe-kv-eastus
        ├── pe-openai-eastus
        └── pe-acr-eastus
```

---

### Prerequisites

- Azure Cloud Shell (bash)
- Terraform >= 1.5.0 (pre-installed in Cloud Shell)
- Contributor access to `rg_award_nomination`

---

### Step 1 — Bootstrap Terraform state storage

```bash
chmod +x bootstrap.sh
./bootstrap.sh
```

---

### Step 2 — Set your local IP

Edit `terraform.tfvars` and set `my_ip` to your current public IP.
Check it at https://whatismyip.com

```hcl
my_ip = "203.0.113.45"   # your actual IP
```

---

### Step 3 — Deploy networking infrastructure

```bash
terraform init
terraform plan    # review what will be created
terraform apply   # type 'yes' to confirm
```

This creates:
- 2 VNets + subnets
- VNet peering (bidirectional)
- 5 private DNS zones linked to both VNets
- 5 private endpoints (SQL, Blob, KV, OpenAI, ACR)
- SQL firewall rule for your local IP
- Storage network rules

**Do NOT disable public access on services yet — verify first.**

---

### Step 4 — Verify private endpoints

```bash
az network private-endpoint list \
  --resource-group rg_award_nomination \
  --query "[].{Name:name, State:privateLinkServiceConnections[0].privateLinkServiceConnectionState.status}" \
  --output table
```

All should show `Approved`.

---

### Step 5 — Lock down KV and OpenAI (manual CLI)

```bash
# Key Vault
az keyvault update \
  --name kv-awardnominations \
  --resource-group rg_award_nomination \
  --default-action Deny \
  --bypass AzureServices

az keyvault network-rule add \
  --name kv-awardnominations \
  --resource-group rg_award_nomination \
  --ip-address YOUR_PUBLIC_IP

# Azure OpenAI
az cognitiveservices account network-rule add \
  --name award-nomination-open-AI \
  --resource-group rg_award_nomination \
  --ip-address YOUR_PUBLIC_IP
```

---

### Step 6 — Recreate CAEs as internal

⚠️ **Your apps will be briefly unavailable during this step.**

```bash
chmod +x cae-recreation.sh
./cae-recreation.sh
```

This script:
1. Deletes existing Container Apps and CAEs
2. Recreates CAEs with VNet injection + internal-only ingress
3. Redeploys Container Apps into new internal CAEs

**Before running** — note down your Container App env vars and secrets
so you can re-add them after recreation.

---

### Step 7 — Connect AFD to internal CAEs via Private Link

This step must be done in the Azure Portal (Terraform AFD Private Link
support is limited):

1. Portal → `Award-Nomination-ADF` → Origin Groups
2. Select your origin group → Edit origin
3. Enable **Private Link**
4. Select the internal CAE load balancer
5. Approve the private endpoint connection in the CAE

---

### Step 8 — Test end to end

```bash
# From your local machine — should still work (whitelisted IP)
sqlcmd -S david64-sql.database.windows.net -U your_user

# Hit the app through AFD
curl https://your-afd-hostname.azurefd.net/health
```

---

### Emergency — open everything back up

If something breaks or you need to demo:

```bash
# SQL — allow all
az sql server firewall-rule create \
  --server david64-sql \
  --resource-group rg_award_nomination \
  --name allow-all-temp \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 255.255.255.255

# Storage — allow all networks
az storage account update \
  --name awardnominationmodels \
  --resource-group rg_award_nomination \
  --default-action Allow

# Key Vault — allow all
az keyvault update \
  --name kv-awardnominations \
  --resource-group rg_award_nomination \
  --default-action Allow
```

---

### Cost impact

| Resource | Monthly cost |
|---|---|
| 2x VNets | Free |
| VNet Peering (minimal traffic) | ~$2 |
| 5x Private Endpoints | ~$25 (5 × $0.01/hr) |
| Private DNS Zones | ~$1 |
| **Total added cost** | **~$28/month** |
