# modules/app-registrations/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure AD App Registrations for non-prod environments
# ─────────────────────────────────────────────────────────────────────────────

data "azuread_client_config" "current" {}

# Stable UUID for the access_as_user OAuth2 scope — persisted in state
resource "random_uuid" "api_scope_id" {}

# ── API app registration ──────────────────────────────────────────────────────
resource "azuread_application" "api" {
  display_name     = "Award Nomination - ${var.environment}"
  sign_in_audience = "AzureADMultipleOrgs"
  owners           = [data.azuread_client_config.current.object_id]
  # identifier_uris is managed by azuread_application_identifier_uri below.
  # ignore_changes prevents azuread_application from clearing the URI when it
  # sends a PATCH — without this, the two resources fight over the same attribute.

  lifecycle {
    ignore_changes = [identifier_uris]
  }

  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      admin_consent_description  = "Allow the application to access the Award Nomination API on behalf of the signed-in user"
      admin_consent_display_name = "Access Award Nomination API"
      enabled                    = true
      id                         = random_uuid.api_scope_id.result
      type                       = "User"
      user_consent_description   = "Allow the application to access the Award Nomination API on your behalf"
      user_consent_display_name  = "Access Award Nomination API"
      value                      = "access_as_user"
    }
  }

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

# Sets api://<clientId> as the Application ID URI.
# Must be a separate resource — can't self-reference client_id inside azuread_application.
resource "azuread_application_identifier_uri" "api" {
  application_id = azuread_application.api.id
  identifier_uri = "api://${azuread_application.api.client_id}"
}

# ── SPA app registration ──────────────────────────────────────────────────────
resource "azuread_application" "frontend" {
  display_name     = "Award Nomination Frontend - ${var.environment}"
  sign_in_audience = "AzureADMultipleOrgs"
  owners           = [data.azuread_client_config.current.object_id]

  api {
    requested_access_token_version = 2
  }

  single_page_application {
    redirect_uris = concat(
      ["http://localhost:5173/", "http://localhost:3000/"],
      var.swa_urls
    )
  }

  # Microsoft Graph — User.Read
  required_resource_access {
    resource_app_id = "00000000-0000-0000-0000-000000000002"

    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" # User.Read
      type = "Scope"
    }
  }

  # Award Nomination API — access_as_user
  required_resource_access {
    resource_app_id = azuread_application.api.client_id

    resource_access {
      id   = random_uuid.api_scope_id.result
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

# Pre-authorize the frontend SPA to call the API without a user consent prompt.
# Without this, the refresh-token grant for api://<api_client_id>/access_as_user
# can fail with consent_required, causing MSAL to fall back to the cached ID token
# instead of obtaining a proper access token (aud = api://<api_client_id>).
resource "azuread_application_pre_authorized" "frontend_to_api" {
  application_id       = azuread_application.api.id
  authorized_client_id = azuread_application.frontend.client_id
  permission_ids       = [random_uuid.api_scope_id.result]
}
