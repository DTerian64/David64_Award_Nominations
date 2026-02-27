# modules/app-registrations/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure AD App Registrations for non-prod environments
#
# Creates:
#   - API app registration (backend — Award Nomination - dev)
#   - SPA app registration (frontend — Award Nomination Frontend - dev)
#   - API scope: access_as_user
#   - SPA redirect URIs pointing to the dev Static Web App URL
#   - Service principal for each app registration
# ─────────────────────────────────────────────────────────────────────────────

data "azuread_client_config" "current" {}

# ── API app registration ──────────────────────────────────────────────────────
resource "azuread_application" "api" {
  display_name = "Award Nomination - ${var.environment}"
  owners       = [data.azuread_client_config.current.object_id]

  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      admin_consent_description  = "Allow the application to access Award Nomination API"
      admin_consent_display_name = "access_as_user"
      enabled                    = true
      id                         = "00000000-0000-0000-0000-000000000001"
      type                       = "User"
      user_consent_description   = "Access Award Nomination API as yourself"
      user_consent_display_name  = "access_as_user"
      value                      = "access_as_user"
    }
  }

  web {
    implicit_grant {
      access_token_issuance_enabled = false
      id_token_issuance_enabled     = true
    }
  }
}

resource "azuread_service_principal" "api" {
  client_id = azuread_application.api.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

# ── SPA app registration ──────────────────────────────────────────────────────
resource "azuread_application" "frontend" {
  display_name = "Award Nomination Frontend - ${var.environment}"
  owners       = [data.azuread_client_config.current.object_id]

  # SPA redirect URIs — points to dev Static Web App + localhost for development
  single_page_application {
    redirect_uris = concat(
      ["http://localhost:5173/", "http://localhost:3000/"],
      var.swa_urls
    )
  }

  # Grant access to the API app
  required_resource_access {
    resource_app_id = azuread_application.api.client_id

    resource_access {
      id   = "00000000-0000-0000-0000-000000000001"
      type = "Scope"
    }
  }

  # Grant access to Microsoft Graph (for user profile)
  required_resource_access {
    resource_app_id = "00000000-0000-0000-0000-000000000002"  # Microsoft Graph

    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d"  # User.Read
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "frontend" {
  client_id = azuread_application.frontend.client_id
  owners    = [data.azuread_client_config.current.object_id]
}
