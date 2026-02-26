# endpoints.tf
# ─────────────────────────────────────────────────────────────────────────────
# Private endpoints for SQL, Blob Storage, Key Vault, OpenAI, ACR
# All deployed into subnet-privatelinks-eastus
# Each endpoint gets a DNS A record in the matching private DNS zone
# ─────────────────────────────────────────────────────────────────────────────

# ── SQL Server ────────────────────────────────────────────────────────────────
resource "azurerm_private_endpoint" "sql" {
  name                = "pe-sql-eastus"
  location            = var.location_east
  resource_group_name = var.resource_group_name
  subnet_id           = azurerm_subnet.private_endpoints_east.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-sql"
    private_connection_resource_id = data.azurerm_sql_server.sql.id
    subresource_names              = ["sqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "dns-group-sql"
    private_dns_zone_ids = [azurerm_private_dns_zone.zones["sql"].id]
  }
}

# ── Blob Storage ──────────────────────────────────────────────────────────────
resource "azurerm_private_endpoint" "blob" {
  name                = "pe-blob-eastus"
  location            = var.location_east
  resource_group_name = var.resource_group_name
  subnet_id           = azurerm_subnet.private_endpoints_east.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-blob"
    private_connection_resource_id = data.azurerm_storage_account.blob.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "dns-group-blob"
    private_dns_zone_ids = [azurerm_private_dns_zone.zones["blob"].id]
  }
}

# ── Key Vault ─────────────────────────────────────────────────────────────────
resource "azurerm_private_endpoint" "kv" {
  name                = "pe-kv-eastus"
  location            = var.location_east
  resource_group_name = var.resource_group_name
  subnet_id           = azurerm_subnet.private_endpoints_east.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-kv"
    private_connection_resource_id = data.azurerm_key_vault.kv.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "dns-group-kv"
    private_dns_zone_ids = [azurerm_private_dns_zone.zones["kv"].id]
  }
}

# ── Azure OpenAI ──────────────────────────────────────────────────────────────
resource "azurerm_private_endpoint" "openai" {
  name                = "pe-openai-eastus"
  location            = var.location_east
  resource_group_name = var.resource_group_name
  subnet_id           = azurerm_subnet.private_endpoints_east.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-openai"
    private_connection_resource_id = data.azurerm_cognitive_account.openai.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "dns-group-openai"
    private_dns_zone_ids = [azurerm_private_dns_zone.zones["openai"].id]
  }
}

# ── Container Registry ────────────────────────────────────────────────────────
resource "azurerm_private_endpoint" "acr" {
  name                = "pe-acr-eastus"
  location            = var.location_east
  resource_group_name = var.resource_group_name
  subnet_id           = azurerm_subnet.private_endpoints_east.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-acr"
    private_connection_resource_id = data.azurerm_container_registry.acr.id
    subresource_names              = ["registry"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "dns-group-acr"
    private_dns_zone_ids = [azurerm_private_dns_zone.zones["acr"].id]
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Service firewall rules — restrict public access, whitelist your local IP
# Apply AFTER private endpoints are confirmed working
# ─────────────────────────────────────────────────────────────────────────────

# ── SQL Server firewall ───────────────────────────────────────────────────────
# Keeps your local IP whitelisted for SSMS and local FastAPI debugging
resource "azurerm_mssql_firewall_rule" "my_ips" {
  for_each         = toset(var.my_ips)
  name             = "allow-${replace(each.value, ".", "-")}"
  server_id        = data.azurerm_sql_server.sql.id
  start_ip_address = each.value
  end_ip_address   = each.value
}

# ── Storage Account network rules ─────────────────────────────────────────────
resource "azurerm_storage_account_network_rules" "blob_rules" {
  storage_account_id = data.azurerm_storage_account.blob.id

  default_action = "Deny"
  bypass         = ["AzureServices"]

  ip_rules = var.my_ips

  virtual_network_subnet_ids = [
    azurerm_subnet.private_endpoints_east.id,
    azurerm_subnet.aca_east.id,
    azurerm_subnet.aca_west.id,
  ]
}

# ── Key Vault network rules ───────────────────────────────────────────────────
# NOTE: Key Vault and OpenAI network rules on EXISTING resources require
# using azurerm_key_vault and azurerm_cognitive_account with lifecycle ignore_changes
# OR using az cli commands provided in outputs.tf
# This avoids Terraform trying to recreate these resources
