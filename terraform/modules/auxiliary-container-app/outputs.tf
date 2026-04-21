# modules/auxiliary-container-app/outputs.tf

output "container_app_id" {
  description = "Auxiliary Container App resource ID"
  value       = azurerm_container_app.auxiliary.id
}

output "container_app_name" {
  description = "Auxiliary Container App name — used by GitHub Actions to update the image after deploy"
  value       = azurerm_container_app.auxiliary.name
}
