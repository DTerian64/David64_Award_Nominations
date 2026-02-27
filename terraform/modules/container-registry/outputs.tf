# modules/container-registry/outputs.tf

output "acr_id" {
  description = "ACR resource ID"
  value       = azurerm_container_registry.acr.id
}

output "acr_name" {
  description = "ACR name"
  value       = azurerm_container_registry.acr.name
}

output "login_server" {
  description = "ACR login server URL — e.g. acrawardnomination.azurecr.io"
  value       = azurerm_container_registry.acr.login_server
}

output "admin_username" {
  description = "ACR admin username — pass to Container Apps for image pull"
  value       = azurerm_container_registry.acr.admin_username
  sensitive   = true
}

output "admin_password" {
  description = "ACR admin password — pass to Container Apps for image pull"
  value       = azurerm_container_registry.acr.admin_password
  sensitive   = true
}

output "private_endpoint_ip" {
  description = "Private IP address of the ACR private endpoint"
  value       = azurerm_private_endpoint.acr.private_service_connection[0].private_ip_address
}
