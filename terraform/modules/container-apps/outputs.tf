# modules/container-apps/outputs.tf

# ── CAE outputs ───────────────────────────────────────────────────────────────
output "cae_east_id" {
  description = "East CAE resource ID"
  value       = azurerm_container_app_environment.east.id
}

output "cae_west_id" {
  description = "West CAE resource ID"
  value       = azurerm_container_app_environment.west.id
}

output "cae_east_static_ip" {
  description = "East CAE internal load balancer static IP — used by AFD Private Link origin"
  value       = azurerm_container_app_environment.east.static_ip_address
}

output "cae_west_static_ip" {
  description = "West CAE internal load balancer static IP — used by AFD Private Link origin"
  value       = azurerm_container_app_environment.west.static_ip_address
}

output "cae_east_default_domain" {
  description = "East CAE default domain"
  value       = azurerm_container_app_environment.east.default_domain
}

output "cae_west_default_domain" {
  description = "West CAE default domain"
  value       = azurerm_container_app_environment.west.default_domain
}

# ── Container App outputs ─────────────────────────────────────────────────────
output "east_app_id" {
  description = "East Container App resource ID"
  value       = azurerm_container_app.east.id
}

output "west_app_id" {
  description = "West Container App resource ID"
  value       = azurerm_container_app.west.id
}

output "east_app_fqdn" {
  description = "East Container App FQDN — internal only"
  value       = azurerm_container_app.east.ingress[0].fqdn
}

output "west_app_fqdn" {
  description = "West Container App FQDN — internal only"
  value       = azurerm_container_app.west.ingress[0].fqdn
}

# ── Managed identity outputs — consumed by key-vault module ──────────────────
output "east_principal_id" {
  description = "East Container App system-assigned managed identity object ID"
  value       = azurerm_container_app.east.identity[0].principal_id
}

output "west_principal_id" {
  description = "West Container App system-assigned managed identity object ID"
  value       = azurerm_container_app.west.identity[0].principal_id
}

# ─────────────────────────────────────────────────────────────────────────────
# POST-DEPLOY NOTE
# ─────────────────────────────────────────────────────────────────────────────
# After terraform apply, Container Apps run the placeholder image.
# GitHub Actions deploys the real image on next push to main.
#
# To manually trigger a deploy without a code push:
#   git commit --allow-empty -m "trigger deploy"
#   git push
#
# Or update the image directly:
#   az containerapp update \
#     --name award-api-eastus \
#     --resource-group rg_award_nomination \
#     --image acrawardnomination.azurecr.io/award-nomination-api:latest
# ─────────────────────────────────────────────────────────────────────────────
