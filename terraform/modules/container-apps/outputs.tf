# modules/container-apps/outputs.tf

# ── CAE outputs ───────────────────────────────────────────────────────────────
output "cae_primary_id" {
  description = "East CAE resource ID"
  value       = azurerm_container_app_environment.primary.id
}

output "cae_secondary_id" {
  description = "West CAE resource ID"
  value       = azurerm_container_app_environment.secondary.id
}

output "cae_primary_static_ip" {
  description = "East CAE internal load balancer static IP — used by AFD Private Link origin"
  value       = azurerm_container_app_environment.primary.static_ip_address
}

output "cae_secondary_static_ip" {
  description = "West CAE internal load balancer static IP — used by AFD Private Link origin"
  value       = azurerm_container_app_environment.secondary.static_ip_address
}

output "cae_primary_default_domain" {
  description = "East CAE default domain"
  value       = azurerm_container_app_environment.primary.default_domain
}

output "cae_secondary_default_domain" {
  description = "West CAE default domain"
  value       = azurerm_container_app_environment.secondary.default_domain
}

# ── Container App outputs ─────────────────────────────────────────────────────
output "primary_app_id" {
  description = "East Container App resource ID"
  value       = azurerm_container_app.primary.id
}

output "secondary_app_id" {
  description = "West Container App resource ID"
  value       = azurerm_container_app.secondary.id
}

output "primary_app_fqdn" {
  description = "East Container App FQDN — used as Front Door origin hostname"
  value       = azurerm_container_app.primary.ingress[0].fqdn
}

output "secondary_app_fqdn" {
  description = "West Container App FQDN — used as Front Door origin hostname"
  value       = azurerm_container_app.secondary.ingress[0].fqdn
}

# ── Managed identity outputs ──────────────────────────────────────────────────
# NOTE: Container Apps now use User-Assigned Managed Identities, which are
# created in the environment main.tf before this module runs. KV access policies
# reference those resources directly — not these outputs.
# The identity_ids passed in are available from the environment main.tf directly.

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
#     --name award-api-primaryus \
#     --resource-group rg_award_nomination \
#     --image acrawardnomination.azurecr.io/award-nomination-api:latest
# ─────────────────────────────────────────────────────────────────────────────
