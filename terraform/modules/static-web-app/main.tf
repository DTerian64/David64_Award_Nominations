# modules/static-web-app/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Static Web App
#
# Creates:
#   - Static Web App (Standard SKU — required for 3+ custom domains)
#   - App settings for React/Vite frontend (Azure AD auth + API URL)
#
# SKU note:
#   Free tier supports 2 custom domains max.
#   Standard tier ($9/month) supports 5 custom domains and is required now
#   that sandbox-awards, acme-awards, and demo-awards are all active.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_static_web_app" "frontend" {
  name                = var.app_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku_tier            = "Standard"
  sku_size            = "Standard"

  app_settings = {
    VITE_API_URL                       = var.vite_api_url
    VITE_API_CLIENT_ID                 = var.vite_api_client_id
    VITE_API_SCOPE                     = var.vite_api_scope
    VITE_CLIENT_ID                     = var.vite_client_id
    VITE_APPINSIGHTS_CONNECTION_STRING = var.vite_appinsights_connection_string
    AI_CLOUD_ROLE                      = var.app_name
  }

  tags = var.tags
}

# Custom domain is managed in the environment's main.tf (not here) so it can
# explicitly depend on the DNS CNAME record being created first.
