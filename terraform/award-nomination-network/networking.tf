# networking.tf
# ─────────────────────────────────────────────────────────────────────────────
# VNets, subnets, and VNet peering
# East US  — ACA east + all PaaS private endpoints
# West US  — ACA west only (reaches East PaaS via peering)
# ─────────────────────────────────────────────────────────────────────────────

# ── East US VNet ──────────────────────────────────────────────────────────────
resource "azurerm_virtual_network" "east" {
  name                = "vnet-award-eastus"
  location            = var.location_east
  resource_group_name = var.resource_group_name
  address_space       = [var.vnet_east_address_space]
  tags                = var.tags
}

# Subnet for East ACA environment
# Must be delegated to Microsoft.App/environments
# Minimum /27 required — /24 gives plenty of room
resource "azurerm_subnet" "aca_east" {
  name                 = "subnet-aca-eastus"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.east.name
  address_prefixes     = ["10.0.1.0/24"]

  delegation {
    name = "aca-delegation"
    service_delegation {
      name = "Microsoft.App/environments"
      actions = [
        "Microsoft.Network/virtualNetworks/subnets/join/action"
      ]
    }
  }
}

# Subnet for all private endpoints (SQL, Storage, KV, OpenAI, ACR)
# Private endpoint subnets must NOT have delegation
resource "azurerm_subnet" "private_endpoints_east" {
  name                 = "subnet-privatelinks-eastus"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.east.name
  address_prefixes     = ["10.0.2.0/24"]

  # Required for private endpoints
  private_endpoint_network_policies = "Disabled"
}

# ── West US VNet ──────────────────────────────────────────────────────────────
resource "azurerm_virtual_network" "west" {
  name                = "vnet-award-westus"
  location            = var.location_west
  resource_group_name = var.resource_group_name
  address_space       = [var.vnet_west_address_space]
  tags                = var.tags
}

# Subnet for West ACA environment
resource "azurerm_subnet" "aca_west" {
  name                 = "subnet-aca-westus"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.west.name
  address_prefixes     = ["10.1.1.0/24"]

  delegation {
    name = "aca-delegation"
    service_delegation {
      name = "Microsoft.App/environments"
      actions = [
        "Microsoft.Network/virtualNetworks/subnets/join/action"
      ]
    }
  }
}

# ── VNet Peering — bidirectional ──────────────────────────────────────────────
# Allows West ACA to reach East private endpoints for SQL, KV, Storage, OpenAI

resource "azurerm_virtual_network_peering" "east_to_west" {
  name                      = "peer-east-to-west"
  resource_group_name       = var.resource_group_name
  virtual_network_name      = azurerm_virtual_network.east.name
  remote_virtual_network_id = azurerm_virtual_network.west.id

  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false
}

resource "azurerm_virtual_network_peering" "west_to_east" {
  name                      = "peer-west-to-east"
  resource_group_name       = var.resource_group_name
  virtual_network_name      = azurerm_virtual_network.west.name
  remote_virtual_network_id = azurerm_virtual_network.east.id

  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false
}
