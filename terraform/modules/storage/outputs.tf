# modules/storage/outputs.tf

output "storage_account_id" {
  description = "Storage account resource ID"
  value       = azurerm_storage_account.storage.id
}

output "storage_account_name" {
  description = "Storage account name"
  value       = azurerm_storage_account.storage.name
}

output "primary_blob_endpoint" {
  description = "Primary blob service endpoint URL"
  value       = azurerm_storage_account.storage.primary_blob_endpoint
}

output "primary_access_key" {
  description = "Primary storage access key — inject into Container App env vars"
  value       = azurerm_storage_account.storage.primary_access_key
  sensitive   = true
}

output "primary_connection_string" {
  description = "Primary connection string — inject into Container App env vars"
  value       = azurerm_storage_account.storage.primary_connection_string
  sensitive   = true
}

output "extracts_container_name" {
  description = "Name of the exports container (PDF/Excel/CSV)"
  value       = azurerm_storage_container.extracts.name
}

output "ml_models_container_name" {
  description = "Name of the ML models container"
  value       = azurerm_storage_container.ml_models.name
}

output "tfstate_container_name" {
  description = "Name of the Terraform state container"
  value       = azurerm_storage_container.tfstate.name
}

output "private_endpoint_ip" {
  description = "Private IP address of the storage private endpoint"
  value       = azurerm_private_endpoint.storage.private_service_connection[0].private_ip_address
}
