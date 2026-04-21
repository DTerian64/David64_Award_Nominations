# Running Kusto Queries from CI/CD

This guide explains how to run Kusto queries programmatically from GitHub Actions, Azure Pipelines, or local scripts.

---

## 🎯 Use Cases

| Use Case | When to Run | Query Examples |
|----------|------------|----------------|
| **Post-Deployment Validation** | After each deployment | `errors/recent-errors.kql` |
| **Daily Health Reports** | Every morning | `monitoring/dashboard-summary.kql` |
| **Alert Monitoring** | Every 15 minutes | All queries in `alerts/` |
| **Weekly Reports** | Sunday night | All monitoring queries |
| **Pre-Release Checks** | Before production deploy | `errors/error-trends.kql` |

---

## 🔧 Option 1: Azure CLI (Bash)

### Prerequisites

```bash
# Install Azure CLI
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# Install Log Analytics extension
az extension add --name log-analytics

# Login
az login
```

### Usage

```bash
# Make script executable
chmod +x run-kusto-query.sh

# Run a query
./run-kusto-query.sh kusto-queries/errors/recent-errors.kql

# Or run directly
WORKSPACE_ID="290020d5-7bb8-4faa-a901-b5da4ad250d7"
QUERY=$(cat kusto-queries/errors/recent-errors.kql)

az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "$QUERY" \
  --output table
```

### Output Formats

```bash
# Table (default)
--output table

# JSON
--output json

# TSV (for parsing)
--output tsv

# YAML
--output yaml
```

---

## 🐍 Option 2: Python SDK

### Prerequisites

```bash
pip install azure-identity azure-monitor-query
```

### Usage

```bash
# Run a query
python run_kusto_query.py errors/recent-errors.kql

# Output as JSON
python run_kusto_query.py fraud/high-risk.kql json

# Output as CSV
python run_kusto_query.py email/email-success-rate.kql csv
```

### In Python Code

```python
from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient
from datetime import timedelta

# Authenticate
credential = DefaultAzureCredential()
client = LogsQueryClient(credential)

# Read query from file
with open('kusto-queries/errors/recent-errors.kql', 'r') as f:
    query = f.read()

# Execute
response = client.query_workspace(
    workspace_id="290020d5-7bb8-4faa-a901-b5da4ad250d7",
    query=query,
    timespan=timedelta(days=7)
)

# Process results
for table in response.tables:
    for row in table.rows:
        print(row)
```

---

## 🚀 Option 3: GitHub Actions

### Setup

1. **Create Azure Service Principal**

```bash
az ad sp create-for-rbac \
  --name "github-actions-award-api" \
  --role "Log Analytics Reader" \
  --scopes /subscriptions/{subscription-id}/resourceGroups/rg_award_nomination \
  --sdk-auth
```

2. **Add to GitHub Secrets**

Go to **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

- Name: `AZURE_CREDENTIALS`
- Value: (paste the JSON output from step 1)

3. **Add Workflows**

Copy the workflow files to `.github/workflows/`:
- `post-deployment-validation.yml`
- `daily-health-report.yml`
- `alert-monitoring.yml`

### Manual Trigger

1. Go to **Actions** tab in GitHub
2. Select workflow (e.g., "Daily Health Report")
3. Click **Run workflow**

### Scheduled Runs

Workflows run automatically:
- **Daily Report**: Every day at 9 AM Pacific
- **Alert Monitoring**: Every 15 minutes

---

## ☁️ Option 4: Azure DevOps Pipelines

### Create Pipeline

```yaml
# azure-pipelines.yml
trigger:
  - main

pool:
  vmImage: 'ubuntu-latest'

steps:
- task: AzureCLI@2
  displayName: 'Run Health Check Query'
  inputs:
    azureSubscription: 'Azure Service Connection'
    scriptType: 'bash'
    scriptLocation: 'inlineScript'
    inlineScript: |
      az extension add --name log-analytics
      
      WORKSPACE_ID="290020d5-7bb8-4faa-a901-b5da4ad250d7"
      QUERY=$(cat kusto-queries/monitoring/dashboard-summary.kql)
      
      az monitor log-analytics query \
        --workspace "$WORKSPACE_ID" \
        --analytics-query "$QUERY" \
        --output table

- task: PublishBuildArtifacts@1
  displayName: 'Publish Query Results'
  inputs:
    PathtoPublish: '$(Build.ArtifactStagingDirectory)'
    ArtifactName: 'kusto-results'
```

---

## 📊 Common Patterns

### Pattern 1: Post-Deployment Validation

```bash
#!/bin/bash
# Check for errors after deployment

echo "Waiting for logs to propagate..."
sleep 120

QUERY='ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(10m)
| where ContainerAppName_s in ("award-api-eastus", "award-api-westus")
| extend LogData = parse_json(Log_s)
| where tostring(LogData.level) == "ERROR"
| summarize ErrorCount = count()'

ERROR_COUNT=$(az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "$QUERY" \
  --output tsv \
  --query '[0].ErrorCount')

if [ "$ERROR_COUNT" -gt 5 ]; then
  echo "❌ Deployment FAILED: $ERROR_COUNT errors detected"
  exit 1
else
  echo "✅ Deployment PASSED: $ERROR_COUNT errors"
fi
```

### Pattern 2: Daily Report Generation

```bash
#!/bin/bash
# Generate daily health report

REPORT_FILE="health-report-$(date +%Y-%m-%d).md"

echo "# Daily Health Report" > "$REPORT_FILE"
echo "Date: $(date)" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Run multiple queries
for QUERY_FILE in kusto-queries/monitoring/*.kql; do
  echo "## $(basename $QUERY_FILE .kql)" >> "$REPORT_FILE"
  ./run-kusto-query.sh "$QUERY_FILE" >> "$REPORT_FILE"
  echo "" >> "$REPORT_FILE"
done

# Send report via email
cat "$REPORT_FILE" | mail -s "Daily Health Report" david.terian@gmail.com
```

### Pattern 3: Alert Monitoring Loop

```bash
#!/bin/bash
# Continuous monitoring (run every 15 minutes via cron)

check_alert() {
  QUERY_FILE=$1
  ALERT_NAME=$2
  
  ./run-kusto-query.sh "$QUERY_FILE" > /tmp/alert-result.txt
  
  if grep -q "YES" /tmp/alert-result.txt; then
    echo "🚨 ALERT: $ALERT_NAME triggered!"
    cat /tmp/alert-result.txt | mail -s "ALERT: $ALERT_NAME" david.terian@gmail.com
  fi
}

# Check all alerts
check_alert "kusto-queries/alerts/error-spike.kql" "Error Spike"
check_alert "kusto-queries/alerts/critical-fraud.kql" "Critical Fraud"
check_alert "kusto-queries/alerts/email-failure-rate.kql" "Email Failures"
```

---

## 🔐 Authentication Methods

### Method 1: Azure CLI Login (Local Development)

```bash
az login
./run-kusto-query.sh kusto-queries/errors/recent-errors.kql
```

### Method 2: Service Principal (CI/CD)

```bash
az login --service-principal \
  --username $AZURE_CLIENT_ID \
  --password $AZURE_CLIENT_SECRET \
  --tenant $AZURE_TENANT_ID

./run-kusto-query.sh kusto-queries/errors/recent-errors.kql
```

### Method 3: Managed Identity (Azure VM/Container)

```bash
# No login needed - uses VM's managed identity
az account show  # Verify identity
./run-kusto-query.sh kusto-queries/errors/recent-errors.kql
```

### Method 4: Environment Variables (Python)

```python
import os
from azure.identity import EnvironmentCredential

os.environ['AZURE_CLIENT_ID'] = 'xxx'
os.environ['AZURE_CLIENT_SECRET'] = 'xxx'
os.environ['AZURE_TENANT_ID'] = 'xxx'

credential = EnvironmentCredential()
# Use credential with LogsQueryClient
```

---

## 📝 Parsing Query Results

### Extract Specific Values (Bash)

```bash
# Get error count from query result
ERROR_COUNT=$(az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "$QUERY" \
  --output tsv \
  --query '[0].ErrorCount')

echo "Errors: $ERROR_COUNT"
```

### Parse JSON (Python)

```python
import json
import subprocess

result = subprocess.run(
    ['az', 'monitor', 'log-analytics', 'query',
     '--workspace', workspace_id,
     '--analytics-query', query,
     '--output', 'json'],
    capture_output=True,
    text=True
)

data = json.loads(result.stdout)
for row in data:
    print(f"Error: {row['Message']}")
```

### Parse with jq (Bash)

```bash
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "$QUERY" \
  --output json | jq '.[] | {message: .Message, count: .ErrorCount}'
```

---

## 🎛️ Advanced Usage

### Run Multiple Queries in Parallel

```bash
#!/bin/bash
# Run all error queries in parallel

for QUERY_FILE in kusto-queries/errors/*.kql; do
  (
    echo "Running $(basename $QUERY_FILE)..."
    ./run-kusto-query.sh "$QUERY_FILE" > "results/$(basename $QUERY_FILE .kql).txt"
  ) &
done

wait
echo "All queries completed"
```

### Export to File

```bash
# Export as JSON
./run-kusto-query.sh kusto-queries/fraud/high-risk.kql \
  | jq '.' > high-risk-nominations.json

# Export as CSV
python run_kusto_query.py kusto-queries/email/email-success-rate.kql csv \
  > email-metrics.csv
```

### Integration with Monitoring Tools

```bash
# Push to Prometheus Pushgateway
METRIC_VALUE=$(./run-kusto-query.sh kusto-queries/errors/recent-errors.kql \
  | grep -oP 'ErrorCount: \K\d+')

echo "api_errors $METRIC_VALUE" | curl --data-binary @- \
  http://pushgateway:9091/metrics/job/award-api

# Send to Datadog
curl -X POST "https://api.datadoghq.com/api/v1/series?api_key=$DD_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"series\": [{
      \"metric\": \"award.api.errors\",
      \"points\": [[$TIMESTAMP, $ERROR_COUNT]]
    }]
  }"
```

---

## 🐛 Troubleshooting

### Issue: "No results returned"

```bash
# Check if logs exist
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "ContainerAppConsoleLogs_CL | take 10" \
  --output table
```

### Issue: "Authentication failed"

```bash
# Verify login
az account show

# Re-login
az login

# Check permissions
az role assignment list --assignee $(az account show --query user.name -o tsv)
```

### Issue: "Query timeout"

```bash
# Reduce time range in query
# Change: | where TimeGenerated > ago(7d)
# To:     | where TimeGenerated > ago(24h)
```

---

## 📚 Additional Resources

- [Azure CLI Log Analytics](https://docs.microsoft.com/en-us/cli/azure/monitor/log-analytics)
- [Azure Monitor Query Python SDK](https://docs.microsoft.com/en-us/python/api/overview/azure/monitor-query-readme)
- [Kusto Query Language](https://docs.microsoft.com/en-us/azure/data-explorer/kusto/query/)
- [GitHub Actions for Azure](https://github.com/Azure/actions)

---

**Last Updated:** February 12, 2026  
**Author:** David Terian
