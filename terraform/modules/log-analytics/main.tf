# modules/log-analytics/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Log Analytics Workspaces
#
# Creates:
#   - Log Analytics workspace Primary (for cae-award-primary)
#   - Log Analytics workspace Secondary (for cae-award-secondary)
#
# Both match existing config: PerGB2018 SKU, 30 day retention
#
# These are consumed by the container-apps module for CAE logging.
# Grafana connects to these workspaces for dashboards.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_log_analytics_workspace" "primary" {
  name                = var.workspace_name_primary
  resource_group_name = var.resource_group_name
  location            = var.location_primary
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_in_days
  tags                = var.tags
}

resource "azurerm_log_analytics_workspace" "secondary" {
  name                = var.workspace_name_secondary
  resource_group_name = var.resource_group_name
  location            = var.location_secondary
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_in_days
  tags                = var.tags
}
