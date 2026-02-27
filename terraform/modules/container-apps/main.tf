# modules/log-analytics/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Log Analytics Workspaces
#
# Creates:
#   - Log Analytics workspace East US (for cae-award-eastus)
#   - Log Analytics workspace West US (for cae-award-westus)
#
# Both match existing config: PerGB2018 SKU, 30 day retention
#
# These are consumed by the container-apps module for CAE logging.
# Grafana connects to these workspaces for dashboards.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_log_analytics_workspace" "east" {
  name                = var.workspace_name_east
  resource_group_name = var.resource_group_name
  location            = var.location_east
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_in_days
  tags                = var.tags
}

resource "azurerm_log_analytics_workspace" "west" {
  name                = var.workspace_name_west
  resource_group_name = var.resource_group_name
  location            = var.location_west
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_in_days
  tags                = var.tags
}
