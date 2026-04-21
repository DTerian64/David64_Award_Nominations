# modules/application-insights/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Application Insights — workspace-based (data stored in Log Analytics)
#
# Creates two resources:
#   - backend  → instruments the FastAPI Container App
#   - frontend → instruments the React/Vite Static Web App
#
# Both are linked to the primary Log Analytics workspace so all telemetry
# (traces, exceptions, custom events, metrics) lands in one place and can be
# queried together in Grafana or Power BI.
#
# Connection strings are output as sensitive values and stored in Key Vault
# (backend) or passed directly as a Vite build-time env var (frontend).
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_application_insights" "backend" {
  name                = "appi-award-api-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location
  workspace_id        = var.log_analytics_workspace_id
  application_type    = "web"
  tags                = var.tags
}

resource "azurerm_application_insights" "frontend" {
  name                = "appi-award-frontend-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location
  workspace_id        = var.log_analytics_workspace_id
  application_type    = "web"
  tags                = var.tags
}
