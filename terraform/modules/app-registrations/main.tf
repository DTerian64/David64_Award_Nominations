# modules/app-registrations/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure AD App Registrations for non-prod environments
# ─────────────────────────────────────────────────────────────────────────────

data "azuread_client_config" "current" {}

# ── API app registration ──────────────────────────────────────────────────────
resource "azuread_application" "api" {
  display_name = "Award Nomination - ${var.environment}"
  owners       = [data.azuread_client_config.current.object_id]

  feature_tags {
    enterprise = true
  }
}

resource "azuread_service_principal" "api" {
  client_id    = azuread_application.api.client_id
  owners       = [data.azuread_client_config.current.object_id]

  feature_tags {
    enterprise = true
  }
}

# ── SPA app registration ──────────────────────────────────────────────────────
resource "azuread_application" "frontend" {
  display_name = "Award Nomination Frontend - ${var.environment}"
  owners       = [data.azuread_client_config.current.object_id]

  single_page_application {
    redirect_uris = concat(
      ["http://localhost:5173/", "http://localhost:3000/"],
      var.swa_urls
    )
  }

  required_resource_access {
    resource_app_id = "00000000-0000-0000-0000-000000000002" # Microsoft Graph

    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" # User.Read
      type = "Scope"
    }
  }

  feature_tags {
    enterprise = true
  }
}

resource "azuread_service_principal" "frontend" {
  client_id = azuread_application.frontend.client_id
  owners    = [data.azuread_client_config.current.object_id]

  feature_tags {
    enterprise = true
  }
}
