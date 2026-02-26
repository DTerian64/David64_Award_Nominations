#!/bin/bash
# bootstrap.sh — uses existing storage account

RESOURCE_GROUP="rg_award_nomination"
STORAGE_ACCOUNT="awardnominationmodels"   # existing storage account
CONTAINER="tfstate"

echo "Creating tfstate container in existing storage account..."

az storage container create \
  --name $CONTAINER \
  --account-name $STORAGE_ACCOUNT

echo "✅ Done. Container 'tfstate' created in $STORAGE_ACCOUNT"