# modules/payout-orchestrator/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# award-payout-orchestrator — Azure Functions on Container Apps (Python 3.11)
#
# Runs the Azure Functions runtime inside the existing Container App Environment
# (no App Service Plan needed — avoids Y1/B1 quota restrictions common on
# sandbox subscriptions).
#
# Orchestrates the two-way payroll integration flow:
#
#   Workflow 1 (outbound):
#     Service Bus award-events/payout-orchestrator  →  NominationPayoutOrchestrator
#     → SubmitPayoutActivity  (POST Workday_Proxy /payouts)
#     → ConfirmPayoutActivity (PATCH Award Nomination API → PaymentSent)
#
#   Workflow 2 (inbound):
#     PayoutWebhookTrigger  (HTTP trigger — Workday_Proxy POSTs here)
#     → PayoutConfirmedActivity (PATCH Award Nomination API → Paid)
#
# Authentication model:
#   User-Assigned Managed Identity (pre-created in environment main.tf).
#   Granted Service Bus Data Receiver + Sender on the award-events topic.
#   Granted Key Vault Get/List via access policy.
#   Storage account uses the access key connection string for Durable Functions
#   state (Table/Queue/Blob) — standard pattern for sandbox environments.
#
# Storage:
#   A dedicated storage account is created for Durable Functions orchestration
#   state. Keeping this separate from the application storage avoids namespace
#   collisions and simplifies access control.
#
# Scaling:
#   min_replicas = 1 — the Functions host manages its own Service Bus polling
#   and long-polling for triggers. KEDA-based scale-to-zero for Functions on
#   ACA requires a TriggerAuthentication resource not yet surfaced in the
#   azurerm provider; 1 replica at 0.25 CPU / 0.5 Gi is negligible cost.
#
# Lifecycle note:
#   Image tag is managed by GitHub Actions.
#   ignore_changes prevents Terraform from resetting the image on every apply.
# ─────────────────────────────────────────────────────────────────────────────

# ── Dedicated storage account for Durable Functions state ─────────────────────
resource "azurerm_storage_account" "fn_storage" {
  name                     = var.storage_account_name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
  tags                     = var.tags
}

# ── Container App running the Azure Functions runtime ─────────────────────────
resource "azurerm_container_app" "payout_orchestrator" {
  name                         = var.function_app_name
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

  # Storage connection string — required by the Azure Functions host for
  # internal state (lease blobs, timers) AND by Durable Functions for
  # orchestration state (instances/history tables, work-item queues).
  secret {
    name  = "azurewebjobsstorage"
    value = azurerm_storage_account.fn_storage.primary_connection_string
  }

  # ── HTTP ingress — Workday_Proxy POSTs to the webhook trigger here ─────────
  # The Functions host listens on port 80 inside the container.
  ingress {
    external_enabled = true
    target_port      = 80
    transport        = "http"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    # Keep 1 replica — the Functions host handles its own Service Bus polling.
    # Scale-to-zero for Functions on ACA requires KEDA TriggerAuthentication
    # which is not yet exposed in the azurerm Terraform provider.
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name  = var.function_app_name
      # Placeholder — GitHub Actions replaces with the built image on first deploy.
      image  = "mcr.microsoft.com/azure-functions/python:4-python3.11"
      cpu    = var.cpu
      memory = var.memory

      # ── Azure Functions runtime ──────────────────────────────────────────
      env {
        name  = "FUNCTIONS_WORKER_RUNTIME"
        value = "python"
      }
      env {
        name  = "FUNCTIONS_EXTENSION_VERSION"
        value = "~4"
      }

      # Storage connection string — passed as a secret reference so the value
      # is never visible in plain-text environment variable listings.
      env {
        name        = "AzureWebJobsStorage"
        secret_name = "azurewebjobsstorage"
      }

      # ── Managed identity ─────────────────────────────────────────────────
      env {
        name  = "AZURE_CLIENT_ID"
        value = var.identity_client_id
      }

      # ── Service Bus ──────────────────────────────────────────────────────
      env {
        name  = "SERVICE_BUS_FQNS"
        value = var.service_bus_fqns
      }
      env {
        name  = "SERVICE_BUS_TOPIC_NAME"
        value = var.service_bus_topic_name
      }
      env {
        name  = "SERVICE_BUS_SUBSCRIPTION_NAME"
        value = var.service_bus_subscription_name
      }

      # ── Downstream services ──────────────────────────────────────────────
      env {
        name  = "AWARD_API_BASE_URL"
        value = var.award_api_base_url
      }
      env {
        name  = "WORKDAY_PROXY_URL"
        value = var.workday_proxy_url
      }

      # ── Observability ────────────────────────────────────────────────────
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.appinsights_connection_string
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = var.function_app_name
      }

      # ── Environment ──────────────────────────────────────────────────────
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
    ]
  }
}
