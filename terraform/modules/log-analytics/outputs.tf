# modules/log-analytics/outputs.tf

# ── East workspace ────────────────────────────────────────────────────────────
output "workspace_primary_id" {
  description = "East Log Analytics workspace resource ID"
  value       = azurerm_log_analytics_workspace.primary.id
}

output "workspace_primary_name" {
  description = "East Log Analytics workspace name"
  value       = azurerm_log_analytics_workspace.primary.name
}

output "workspace_primary_customer_id" {
  description = "East workspace customer ID — used by CAE for log destination"
  value       = azurerm_log_analytics_workspace.primary.workspace_id
}

output "workspace_primary_primary_key" {
  description = "East workspace primary shared key — used by CAE for log destination"
  value       = azurerm_log_analytics_workspace.primary.primary_shared_key
  sensitive   = true
}

# ── West workspace ────────────────────────────────────────────────────────────
output "workspace_secondary_id" {
  description = "West Log Analytics workspace resource ID"
  value       = azurerm_log_analytics_workspace.secondary.id
}

output "workspace_secondary_name" {
  description = "West Log Analytics workspace name"
  value       = azurerm_log_analytics_workspace.secondary.name
}

output "workspace_secondary_customer_id" {
  description = "West workspace customer ID — used by CAE for log destination"
  value       = azurerm_log_analytics_workspace.secondary.workspace_id
}

output "workspace_secondary_primary_key" {
  description = "West workspace primary shared key — used by CAE for log destination"
  value       = azurerm_log_analytics_workspace.secondary.primary_shared_key
  sensitive   = true
}
