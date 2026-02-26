# private_dns.tf
# ─────────────────────────────────────────────────────────────────────────────
# Private DNS zones — one per PaaS service type.
# Each zone is linked to BOTH VNets so East and West ACAs can resolve
# private endpoint hostnames to their private IPs.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  dns_zones = {
    sql     = "privatelink.database.windows.net"
    blob    = "privatelink.blob.core.windows.net"
    kv      = "privatelink.vaultcore.azure.net"
    openai  = "privatelink.openai.azure.com"
    acr     = "privatelink.azurecr.io"
  }
}

# ── Create one private DNS zone per service ───────────────────────────────────
resource "azurerm_private_dns_zone" "zones" {
  for_each            = local.dns_zones
  name                = each.value
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

# ── Link each zone to East US VNet ────────────────────────────────────────────
resource "azurerm_private_dns_zone_virtual_network_link" "east_links" {
  for_each = local.dns_zones

  name                  = "link-${each.key}-eastus"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.zones[each.key].name
  virtual_network_id    = azurerm_virtual_network.east.id
  registration_enabled  = false
  tags                  = var.tags
}

# ── Link each zone to West US VNet ────────────────────────────────────────────
# West ACA needs to resolve the same private DNS names via peering
resource "azurerm_private_dns_zone_virtual_network_link" "west_links" {
  for_each = local.dns_zones

  name                  = "link-${each.key}-westus"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.zones[each.key].name
  virtual_network_id    = azurerm_virtual_network.west.id
  registration_enabled  = false
  tags                  = var.tags
}
