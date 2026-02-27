# modules/grafana/outputs.tf

output "grafana_id" {
  description = "Grafana workspace resource ID"
  value       = azurerm_dashboard_grafana.grafana.id
}

output "grafana_name" {
  description = "Grafana workspace name"
  value       = azurerm_dashboard_grafana.grafana.name
}

output "grafana_endpoint" {
  description = "Grafana dashboard URL — open in browser to access dashboards"
  value       = azurerm_dashboard_grafana.grafana.endpoint
}

output "grafana_principal_id" {
  description = "Grafana managed identity — used to grant access to other resources"
  value       = azurerm_dashboard_grafana.grafana.identity[0].principal_id
}

# ─────────────────────────────────────────────────────────────────────────────
# POST-DEPLOY NOTE — Register provider if apply fails
# ─────────────────────────────────────────────────────────────────────────────
# If terraform apply fails with "subscription not registered":
#
#   az provider register --namespace Microsoft.Dashboard
#   az provider show --namespace Microsoft.Dashboard --query registrationState
#
# Wait for "Registered" then re-run terraform apply.
#
# POST-DEPLOY NOTE — Grant team members Grafana access
# ─────────────────────────────────────────────────────────────────────────────
# Add team members via portal or az cli:
#
#   az role assignment create \
#     --assignee user@domain.com \
#     --role "Grafana Viewer" \
#     --scope <grafana_id output>
# ─────────────────────────────────────────────────────────────────────────────
