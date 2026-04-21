# modules/workday-proxy/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# workday-proxy — Azure Container App (HTTP ingress, single region)
#
# A lightweight FastAPI service that mimics the Workday payroll API surface
# for sandbox and dev environments.  When real Workday integration is ready,
# only the Logic App / Durable Function target URL needs to change — this
# service is decommissioned with no changes to any other service.
#
# Exposes:
#   POST /payouts            — accept a payout submission, return paymentRef
#   GET  /payouts/{ref}      — query payment status
#   (webhook callback to payout-orchestrator is triggered internally after
#    simulated processing delay)
#
# Authentication model:
#   User-Assigned Managed Identity (pre-created in environment main.tf).
#   External HTTP ingress is enabled — the payout-orchestrator calls this
#   service over HTTPS using the default Container Apps hostname.
#   In sandbox, no additional auth is enforced on the proxy itself; the
#   orchestrator passes an internal shared secret via X-Api-Key header
#   (injected as WORKDAY_PROXY_API_KEY env var on both sides).
#
# Lifecycle note:
#   Image tag is managed by GitHub Actions. ignore_changes prevents Terraform
#   from resetting the image to the placeholder on every apply.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_container_app" "workday_proxy" {
  name                         = var.app_name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.container_app_environment_id
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  # ACR credentials for image pull
  registry {
    server               = var.acr_login_server
    username             = var.acr_admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_admin_password
  }

  # ── External HTTP ingress ──────────────────────────────────────────────────
  # The payout-orchestrator (and any test client) calls this service over HTTPS.
  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "http"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = var.app_name
      image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
      cpu    = var.cpu
      memory = var.memory

      env {
        name  = "AZURE_CLIENT_ID"
        value = var.identity_client_id
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = var.app_name
      }

      # Award API webhook — Workday_Proxy POSTs to {base}/api/webhooks/workday/
      # payment-confirmed after its simulated processing delay.
      # In production: register this URL with real Workday instead.
      env {
        name  = "AWARD_API_BASE_URL"
        value = var.award_api_base_url
      }

      # Application Insights
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.appinsights_connection_string
      }

      # Caller-supplied env vars (e.g. feature flags, processing delay seconds)
      dynamic "env" {
        for_each = var.environment_variables
        content {
          name  = env.value.name
          value = env.value.value
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
    ]
  }
}
