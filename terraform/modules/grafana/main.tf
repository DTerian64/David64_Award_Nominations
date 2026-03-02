# modules/grafana/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Managed Grafana
# Note: grafana_major_version omitted — let Azure use its current default
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_dashboard_grafana" "grafana" {
  name                = var.grafana_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Standard"

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

data "azurerm_client_config" "current" {}

resource "azurerm_role_assignment" "grafana_admin" {
  scope                = azurerm_dashboard_grafana.grafana.id
  role_definition_name = "Grafana Admin"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "grafana_monitoring_reader_east" {
  scope                = var.log_analytics_workspace_east_id
  role_definition_name = "Monitoring Reader"
  principal_id         = azurerm_dashboard_grafana.grafana.identity[0].principal_id
}

resource "azurerm_role_assignment" "grafana_monitoring_reader_west" {
  scope                = var.log_analytics_workspace_west_id
  role_definition_name = "Monitoring Reader"
  principal_id         = azurerm_dashboard_grafana.grafana.identity[0].principal_id
}
