#!/bin/bash
# scripts/bootstrap.sh
# ─────────────────────────────────────────────────────────────────────────────
# Run ONCE before terraform init for any environment.
# Creates the tfstate container in the existing storage account.
# ─────────────────────────────────────────────────────────────────────────────

set -e

RESOURCE_GROUP="rg_award_nomination"
STORAGE_ACCOUNT="awardnominationmodels"
CONTAINER="tfstate"

echo "Creating tfstate container in $STORAGE_ACCOUNT..."

az storage container create \
  --name $CONTAINER \
  --account-name $STORAGE_ACCOUNT

echo ""
echo "✅ Done. State container ready."
echo "   Run: terraform init in environments/prod or environments/dev"
