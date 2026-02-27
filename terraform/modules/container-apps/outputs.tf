# modules/log-analytics/outputs.tf

# ── East workspace ────────────────────────────────────────────────────────────
output "workspace_east_id" {
  description = "East Log Analytics workspace resource ID"
  value       = azurerm_log_analytics_workspace.east.id
}

output "workspace_east_name" {
  description = "East Log Analytics workspace name"
  value       = azurerm_log_analytics_workspace.east.name
}

output "workspace_east_customer_id" {
  description = "East workspace customer ID — used by CAE for log destination"
  value       = azurerm_log_analytics_workspace.east.workspace_id
}

output "workspace_east_primary_key" {
  description = "East workspace primary shared key — used by CAE for log destination"
  value       = azurerm_log_analytics_workspace.east.primary_shared_key
  sensitive   = true
}

# ── West workspace ────────────────────────────────────────────────────────────
output "workspace_west_id" {
  description = "West Log Analytics workspace resource ID"
  value       = azurerm_log_analytics_workspace.west.id
}

output "workspace_west_name" {
  description = "West Log Analytics workspace name"
  value       = azurerm_log_analytics_workspace.west.name
}

output "workspace_west_customer_id" {
  description = "West workspace customer ID — used by CAE for log destination"
  value       = azurerm_log_analytics_workspace.west.workspace_id
}

output "workspace_west_primary_key" {
  description = "West workspace primary shared key — used by CAE for log destination"
  value       = azurerm_log_analytics_workspace.west.primary_shared_key
  sensitive   = true
}
