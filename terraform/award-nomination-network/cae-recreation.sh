#!/bin/bash
# cae-recreation.sh
# ─────────────────────────────────────────────────────────────────────────────
# Recreates Container App Environments as internal (VNet injected).
# Run AFTER terraform apply — you need the subnet IDs from terraform output.
#
# ⚠️  WARNING: This DELETES and RECREATES the CAEs and all Container Apps
#     in them. Your apps will be down briefly. Plan for a maintenance window.
#
# Before running:
#   1. Note your current Container App environment variables / secrets
#   2. Note your current Container App scaling rules and ingress settings
#   3. Have your container image tag ready (e.g. acrawardnomination.azurecr.io/award-api:latest)
# ─────────────────────────────────────────────────────────────────────────────

set -e

RG="rg_award_nomination"
ACR="acrawardnomination.azurecr.io"
IMAGE="award-nomination-api:latest"

# Get subnet IDs from terraform output
SUBNET_EAST=$(terraform output -raw subnet_aca_east_id)
SUBNET_WEST=$(terraform output -raw subnet_aca_west_id)

echo "Subnet East: $SUBNET_EAST"
echo "Subnet West: $SUBNET_WEST"
echo ""
echo "⚠️  This will delete and recreate your CAEs and Container Apps."
read -p "Are you sure? Type 'yes' to continue: " confirm
if [ "$confirm" != "yes" ]; then
  echo "Aborted."
  exit 0
fi

# ── Step 1: Delete existing Container Apps ────────────────────────────────────
echo "Deleting existing Container Apps..."
az containerapp delete --name award-api-eastus --resource-group $RG --yes
az containerapp delete --name award-api-westus --resource-group $RG --yes

# ── Step 2: Delete existing CAEs ──────────────────────────────────────────────
echo "Deleting existing Container App Environments..."
az containerapp env delete --name cae-award-eastus --resource-group $RG --yes
az containerapp env delete --name cae-award-westus --resource-group $RG --yes

# ── Step 3: Recreate CAEs as internal with VNet injection ─────────────────────
echo "Creating internal CAE in East US..."
az containerapp env create \
  --name cae-award-eastus \
  --resource-group $RG \
  --location eastus \
  --infrastructure-subnet-resource-id $SUBNET_EAST \
  --internal-only true \
  --logs-destination log-analytics \
  --logs-workspace-id $(az monitor log-analytics workspace show \
      --workspace-name workspace-rgawardnomination6aem \
      --resource-group $RG \
      --query customerId -o tsv) \
  --logs-workspace-key $(az monitor log-analytics workspace get-shared-keys \
      --workspace-name workspace-rgawardnomination6aem \
      --resource-group $RG \
      --query primarySharedKey -o tsv)

echo "Creating internal CAE in West US..."
az containerapp env create \
  --name cae-award-westus \
  --resource-group $RG \
  --location westus \
  --infrastructure-subnet-resource-id $SUBNET_WEST \
  --internal-only true \
  --logs-destination log-analytics \
  --logs-workspace-id $(az monitor log-analytics workspace show \
      --workspace-name workspace-rgawardnomination57mY \
      --resource-group $RG \
      --query customerId -o tsv) \
  --logs-workspace-key $(az monitor log-analytics workspace get-shared-keys \
      --workspace-name workspace-rgawardnomination57mY \
      --resource-group $RG \
      --query primarySharedKey -o tsv)

# ── Step 4: Redeploy Container Apps into new internal CAEs ────────────────────
echo "Redeploying award-api-eastus..."
az containerapp create \
  --name award-api-eastus \
  --resource-group $RG \
  --environment cae-award-eastus \
  --image $ACR/$IMAGE \
  --registry-server $ACR \
  --ingress internal \
  --target-port 8000 \
  --min-replicas 1 \
  --max-replicas 3 \
  --cpu 0.5 \
  --memory 1.0Gi

echo "Redeploying award-api-westus..."
az containerapp create \
  --name award-api-westus \
  --resource-group $RG \
  --environment cae-award-westus \
  --image $ACR/$IMAGE \
  --registry-server $ACR \
  --ingress internal \
  --target-port 8000 \
  --min-replicas 1 \
  --max-replicas 3 \
  --cpu 0.5 \
  --memory 1.0Gi

echo ""
echo "✅ CAEs and Container Apps recreated as internal."
echo ""
echo "Next: Update AFD origins to use Private Link to the new internal CAEs."
echo "This must be done in the Azure Portal:"
echo "  AFD → Origin Groups → your origin → Enable Private Link"
