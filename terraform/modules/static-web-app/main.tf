# modules/static-web-app/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Static Web App
#
# Creates:
#   - Static Web App (Free SKU)
#   - App settings for React/Vite frontend (Azure AD auth + API URL)
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_static_web_app" "frontend" {
  name                = var.app_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku_tier            = "Free"
  sku_size            = "Free"

  app_settings = {
    VITE_API_URL       = var.vite_api_url
    VITE_API_CLIENT_ID = var.vite_api_client_id
    VITE_API_SCOPE     = var.vite_api_scope
    VITE_CLIENT_ID     = var.vite_client_id
    # VITE_TENANT_ID removed — frontend now uses /organizations authority
    # so no tenant ID is needed at build time.
  }

  tags = var.tags
}

# Custom domain is managed in the environment's main.tf (not here) so it can
# explicitly depend on the DNS CNAME record being created first.
