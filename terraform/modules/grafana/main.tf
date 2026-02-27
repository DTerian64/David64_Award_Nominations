# modules/static-web-app/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Static Web App
#
# Creates:
#   - Static Web App (Free SKU — matches award-nomination-frontend)
#   - Linked to GitHub repo + branch for automated deployments
#
# GitHub Actions integration:
#   Azure generates a deployment token on creation. This token is
#   automatically added to the GitHub repo as a secret by the resource.
#   GitHub Actions workflow uses this token to deploy the React SPA.
#
# NOTE: Free SKU limitations:
#   - 100GB bandwidth/month
#   - No custom authentication providers
#   - No staging environments
#   Upgrade to Standard if you need staging slots or custom auth.
#
# The AFD hostname is injected as an app setting so the React app
# knows where to call the backend API.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_static_web_app" "frontend" {
  name                = var.app_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku_tier            = "Free"
  sku_size            = "Free"

  tags = var.tags
}

# ── App settings — environment variables for the React build ─────────────────
resource "azurerm_static_web_app_custom_domain" "domain" {
  count              = var.custom_domain != "" ? 1 : 0
  static_web_app_id  = azurerm_static_web_app.frontend.id
  domain_name        = var.custom_domain
  validation_type    = "cname-delegation"
}
