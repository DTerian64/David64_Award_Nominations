#!/bin/bash
# run-kusto-query.sh
# Description: Run a Kusto query from a .kql file using Azure CLI
# Usage: ./run-kusto-query.sh <query-file.kql>

set -e

# Configuration
WORKSPACE_NAME="workspace-rgawardnomination6aem"
RESOURCE_GROUP="rg_award_nomination"
QUERY_FILE="${1}"

if [ -z "$QUERY_FILE" ]; then
    echo "Usage: $0 <query-file.kql>"
    echo "Example: $0 errors/recent-errors.kql"
    exit 1
fi

if [ ! -f "$QUERY_FILE" ]; then
    echo "Error: Query file not found: $QUERY_FILE"
    exit 1
fi

echo "📊 Running Kusto query from: $QUERY_FILE"
echo "🏢 Workspace: $WORKSPACE_NAME"
echo ""

# Get workspace ID
WORKSPACE_ID=$(az monitor log-analytics workspace show \
    --workspace-name "$WORKSPACE_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query customerId \
    --output tsv)

echo "🆔 Workspace ID: $WORKSPACE_ID"

# Read query from file (remove comments for cleaner output)
QUERY=$(cat "$QUERY_FILE")

echo "🔍 Executing query..."
echo ""

# Run the query
az monitor log-analytics query \
    --workspace "$WORKSPACE_ID" \
    --analytics-query "$QUERY" \
    --output table

echo ""
echo "✅ Query completed successfully"
