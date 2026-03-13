# modules/networking/outputs.tf

# ── VNet IDs ──────────────────────────────────────────────────────────────────
output "vnet_primary_id" {
  description = "East US VNet resource ID"
  value       = azurerm_virtual_network.primary.id
}

output "vnet_secondary_id" {
  description = "West US VNet resource ID"
  value       = azurerm_virtual_network.secondary.id
}

# ── Subnet IDs — consumed by container-apps and other modules ─────────────────
output "subnet_aca_primary_id" {
  description = "East ACA subnet ID — pass to container-apps module"
  value       = azurerm_subnet.aca_primary.id
}

output "subnet_aca_secondary_id" {
  description = "West ACA subnet ID — pass to container-apps module"
  value       = azurerm_subnet.aca_secondary.id
}

output "subnet_private_endpoints_id" {
  description = "Private endpoints subnet ID — pass to sql, storage, kv, openai, acr modules"
  value       = azurerm_subnet.private_endpoints.id
}

# ── Private DNS Zone IDs — consumed by each PaaS module ──────────────────────
output "dns_zone_sql_id" {
  description = "SQL private DNS zone ID"
  value       = azurerm_private_dns_zone.zones["sql"].id
}

output "dns_zone_blob_id" {
  description = "Blob storage private DNS zone ID"
  value       = azurerm_private_dns_zone.zones["blob"].id
}

output "dns_zone_kv_id" {
  description = "Key Vault private DNS zone ID"
  value       = azurerm_private_dns_zone.zones["kv"].id
}

output "dns_zone_openai_id" {
  description = "OpenAI private DNS zone ID"
  value       = azurerm_private_dns_zone.zones["openai"].id
}

output "dns_zone_acr_id" {
  description = "ACR private DNS zone ID"
  value       = azurerm_private_dns_zone.zones["acr"].id
}
