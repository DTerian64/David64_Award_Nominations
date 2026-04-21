# modules/workday-proxy/outputs.tf

output "app_name" {
  description = "Container App name"
  value       = azurerm_container_app.workday_proxy.name
}

output "app_id" {
  description = "Container App resource ID"
  value       = azurerm_container_app.workday_proxy.id
}

output "fqdn" {
  description = "External FQDN of the Workday_Proxy service — inject as WORKDAY_PROXY_URL in the payout-orchestrator"
  value       = azurerm_container_app.workday_proxy.ingress[0].fqdn
}

output "base_url" {
  description = "Full HTTPS base URL of the Workday_Proxy service"
  value       = "https://${azurerm_container_app.workday_proxy.ingress[0].fqdn}"
}
