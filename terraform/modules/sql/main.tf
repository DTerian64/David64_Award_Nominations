# modules/sql/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure SQL Server + Database
#
# Creates:
#   - SQL Server (azurerm_mssql_server)
#   - SQL Database (azurerm_mssql_database) — Serverless GP_S_Gen5
#   - Private endpoint → subnet-privatelinks
#   - Private DNS zone group registration
#   - Firewall rules for local IP whitelist
#   - Azure Services access (for ACAs)
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_mssql_server" "sql" {
  name                         = var.server_name
  resource_group_name          = var.resource_group_name
  location                     = var.location
  version                      = "12.0"
  administrator_login          = var.admin_login
  administrator_login_password = var.admin_password

  # Disable public access once private endpoint is confirmed working
  # Set to false after initial deployment and testing
  public_network_access_enabled = var.public_network_access_enabled

  tags = var.tags
}

resource "azurerm_mssql_database" "db" {
  name         = var.database_name
  server_id    = azurerm_mssql_server.sql.id
  collation    = "SQL_Latin1_General_CP1_CI_AS"
  license_type = "LicenseIncluded"
  max_size_gb  = 32
  zone_redundant = false

  # Serverless — matches existing GP_S_Gen5 2 vCores
  sku_name                    = "GP_S_Gen5_2"
  auto_pause_delay_in_minutes = 60
  min_capacity                = 0.5

  tags = var.tags
}

# ── Private endpoint ──────────────────────────────────────────────────────────
resource "azurerm_private_endpoint" "sql" {
  name                = "pe-${var.server_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-${var.server_name}"
    private_connection_resource_id = azurerm_mssql_server.sql.id
    subresource_names              = ["sqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "dns-group-sql"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }
}

# ── Firewall rules ────────────────────────────────────────────────────────────
# One rule per IP in the whitelist
resource "azurerm_mssql_firewall_rule" "my_ips" {
  for_each         = toset(var.allowed_ips)
  name             = "allow-${replace(each.value, ".", "-")}"
  server_id        = azurerm_mssql_server.sql.id
  start_ip_address = each.value
  end_ip_address   = each.value
}

# Allow Azure services (ACAs, GitHub Actions etc.)
resource "azurerm_mssql_firewall_rule" "azure_services" {
  name             = "allow-azure-services"
  server_id        = azurerm_mssql_server.sql.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}
