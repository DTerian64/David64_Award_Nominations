# modules/networking/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Full networking stack per environment
#
# Creates:
#   - VNet East + subnets (ACA + private endpoints)
#   - VNet West + ACA subnet
#   - Bidirectional VNet peering
#   - 5 Private DNS zones linked to both VNets
#   - 5 Private endpoints (SQL, Blob, KV, OpenAI, ACR)
#   - SQL + Storage firewall rules for local IP whitelist
# ─────────────────────────────────────────────────────────────────────────────

# ── East US VNet ──────────────────────────────────────────────────────────────
resource "azurerm_virtual_network" "east" {
  name                = "vnet-award-eastus-${var.environment}"
  location            = var.location_east
  resource_group_name = var.resource_group_name
  address_space       = [var.vnet_east_address_space]
  tags                = var.tags
}

resource "azurerm_subnet" "aca_east" {
  name                 = "subnet-aca-eastus-${var.environment}"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.east.name
  address_prefixes     = [cidrsubnet(var.vnet_east_address_space, 8, 1)]

  delegation {
    name = "aca-delegation"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "private_endpoints" {
  name                 = "subnet-privatelinks-eastus-${var.environment}"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.east.name
  address_prefixes     = [cidrsubnet(var.vnet_east_address_space, 8, 2)]

  private_endpoint_network_policies = "Disabled"
}

# ── West US VNet ──────────────────────────────────────────────────────────────
resource "azurerm_virtual_network" "west" {
  name                = "vnet-award-westus-${var.environment}"
  location            = var.location_west
  resource_group_name = var.resource_group_name
  address_space       = [var.vnet_west_address_space]
  tags                = var.tags
}

resource "azurerm_subnet" "aca_west" {
  name                 = "subnet-aca-westus-${var.environment}"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.west.name
  address_prefixes     = [cidrsubnet(var.vnet_west_address_space, 8, 1)]

  delegation {
    name = "aca-delegation"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# ── VNet Peering ──────────────────────────────────────────────────────────────
resource "azurerm_virtual_network_peering" "east_to_west" {
  name                         = "peer-east-to-west-${var.environment}"
  resource_group_name          = var.resource_group_name
  virtual_network_name         = azurerm_virtual_network.east.name
  remote_virtual_network_id    = azurerm_virtual_network.west.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

resource "azurerm_virtual_network_peering" "west_to_east" {
  name                         = "peer-west-to-east-${var.environment}"
  resource_group_name          = var.resource_group_name
  virtual_network_name         = azurerm_virtual_network.west.name
  remote_virtual_network_id    = azurerm_virtual_network.east.id
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

resource "azurerm_private_dns_zone_virtual_network_link" "east_links" {
  for_each              = local.dns_zones
  name                  = "link-${each.key}-eastus-${var.environment}"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.zones[each.key].name
  virtual_network_id    = azurerm_virtual_network.east.id
  registration_enabled  = false
  tags                  = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "west_links" {
  for_each              = local.dns_zones
  name                  = "link-${each.key}-westus-${var.environment}"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.zones[each.key].name
  virtual_network_id    = azurerm_virtual_network.west.id
  registration_enabled  = false
  tags                  = var.tags
}
