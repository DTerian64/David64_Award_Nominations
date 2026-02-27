# modules/grafana/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Managed Grafana
#
# Creates:
#   - Grafana workspace (Standard SKU — matches awardnomination-grafana)
#   - Links to both Log Analytics workspaces as data sources
#
# Access:
#   Grafana uses Azure AD for authentication. Users need the
#   "Grafana Admin", "Grafana Editor", or "Grafana Viewer" role
#   assigned on the Grafana resource to log in.
#
# NOTE: The azurerm_dashboard_grafana resource requires the
#   "Azure Managed Grafana" provider feature to be registered:
#   az provider register --namespace Microsoft.Dashboard
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_dashboard_grafana" "grafana" {
  name                = var.grafana_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Standard"

  # Allows Grafana to query Azure Monitor / Log Analytics
  azure_monitor_workspace_integrations {
    resource_id = var.log_analytics_workspace_east_id
  }

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

# ── Grafana Admin role for the deploying user ─────────────────────────────────
data "azurerm_client_config" "current" {}

resource "azurerm_role_assignment" "grafana_admin" {
  scope                = azurerm_dashboard_grafana.grafana.id
  role_definition_name = "Grafana Admin"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ── Monitoring Reader — lets Grafana query Log Analytics workspaces ───────────
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
