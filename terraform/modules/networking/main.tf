# modules/networking/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Full networking stack per environment
#
# Creates:
#   - VNet East + subnets (ACA + private endpoints)
#   - VNet West + ACA subnet
#   - Bidirectional VNet peering
#   - 5 Private DNS zones linked to both VNets
# ─────────────────────────────────────────────────────────────────────────────

# ── East US VNet ──────────────────────────────────────────────────────────────
resource "azurerm_virtual_network" "primary" {
  name                = "vnet-award-primaryus-${var.environment}"
  location            = var.location_primary
  resource_group_name = var.resource_group_name
  address_space       = [var.vnet_primary_address_space]
  tags                = var.tags
}

resource "azurerm_subnet" "aca_primary" {
  name                 = "subnet-aca-primaryus-${var.environment}"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.primary.name
  address_prefixes     = [cidrsubnet(var.vnet_primary_address_space, 8, 1)]

  # Service endpoints allow ACA to access KV and Storage directly
  service_endpoints = [
    "Microsoft.KeyVault",
    "Microsoft.Storage",
  ]

  delegation {
    name = "aca-delegation"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "private_endpoints" {
  name                 = "subnet-privatelinks-primaryus-${var.environment}"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.primary.name
  address_prefixes     = [cidrsubnet(var.vnet_primary_address_space, 8, 2)]

  private_endpoint_network_policies = "Disabled"
}

# ── West US VNet ──────────────────────────────────────────────────────────────
resource "azurerm_virtual_network" "secondary" {
  name                = "vnet-award-secondaryus-${var.environment}"
  location            = var.location_secondary
  resource_group_name = var.resource_group_name
  address_space       = [var.vnet_secondary_address_space]
  tags                = var.tags
}

resource "azurerm_subnet" "aca_secondary" {
  name                 = "subnet-aca-secondaryus-${var.environment}"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.secondary.name
  address_prefixes     = [cidrsubnet(var.vnet_secondary_address_space, 8, 1)]

  # Service endpoints allow ACA to access KV and Storage directly
  service_endpoints = [
    "Microsoft.KeyVault",
    "Microsoft.Storage",
  ]

  delegation {
    name = "aca-delegation"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# ── VNet Peering ──────────────────────────────────────────────────────────────
resource "azurerm_virtual_network_peering" "primary_to_secondary" {
  name                         = "peer-primary-to-secondary-${var.environment}"
  resource_group_name          = var.resource_group_name
  virtual_network_name         = azurerm_virtual_network.primary.name
  remote_virtual_network_id    = azurerm_virtual_network.secondary.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

resource "azurerm_virtual_network_peering" "secondary_to_primary" {
  name                         = "peer-secondary-to-primary-${var.environment}"
  resource_group_name          = var.resource_group_name
  virtual_network_name         = azurerm_virtual_network.secondary.name
  remote_virtual_network_id    = azurerm_virtual_network.primary.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

# ── Private DNS Zones ─────────────────────────────────────────────────────────
locals {
  dns_zones = {
    sql    = "privatelink.database.windows.net"
    blob   = "privatelink.blob.core.windows.net"
    kv     = "privatelink.vaultcore.azure.net"
    openai = "privatelink.openai.azure.com"
    acr    = "privatelink.azurecr.io"
  }
}

resource "azurerm_private_dns_zone" "zones" {
  for_each            = local.dns_zones
  name                = each.value
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "primary_links" {
  for_each              = local.dns_zones
  name                  = "link-${each.key}-primaryus-${var.environment}"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.zones[each.key].name
  virtual_network_id    = azurerm_virtual_network.primary.id
  registration_enabled  = false
  tags                  = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "secondary_links" {
  for_each              = local.dns_zones
  name                  = "link-${each.key}-secondaryus-${var.environment}"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.zones[each.key].name
  virtual_network_id    = azurerm_virtual_network.secondary.id
  registration_enabled  = false
  tags                  = var.tags
}
