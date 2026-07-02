# modules/payroll-broker/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Payroll Broker Container App
#
# Dual-role ACA:
#   - HTTP ingress  — receives provider callback webhooks routed via AFD
#     (payroll-broker.terianix.ai/gusto/webhook, /gusto/callback, etc.)
#   - Service Bus consumer — KEDA-driven; picks up nomination.approved events
#     from the payroll-processor subscription and calls the provider REST API
#
# Design rationale:
#   The broker owns the ENTIRE payroll conversation: outbound API call to the
#   provider (step 3) and inbound webhook from the provider (step 4). This
#   keeps all payroll-provider logic in one place. Adding a new provider
#   (Workday, Rippling, etc.) means adding a route handler here — no changes
#   to the backend, Service Bus topology, or AFD configuration.
#
# Authentication model:
#   User-Assigned Managed Identity — pre-created before this module runs.
#   Grants: Service Bus Data Receiver (payroll-processor subscription)
#           Service Bus Data Sender   (publishes payroll.accepted / payroll.failed)
#           Key Vault Get/List        (Gusto client secret, webhook secret)
#
# Scaling:
#   min_replicas = 1 (enforced by default — see variables.tf comment).
#   The HTTP endpoint must always be reachable for provider webhook callbacks.
#   Scale-to-zero would cause Gusto to receive 502s and retry indefinitely.
#   KEDA scales upward from 1 when payroll-processor messages accumulate.
#
# Tenant routing:
#   Each tenant row in dbo.Tenants stores payroll_provider + encrypted tokens.
#   The broker reads tenant config at event time and dispatches to the correct
#   provider adapter (Gusto today; extensible without infrastructure changes).
#
# Lifecycle note:
#   Image is managed by GitHub Actions — ignore_changes prevents terraform
#   apply from resetting the image to the placeholder after first deploy.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_container_app" "payroll_broker" {
  name                         = var.app_name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.container_app_environment_id
  revision_mode                = "Single"
  tags                         = var.tags

  # User-assigned MI — pre-authorized for KV and Service Bus before this
  # resource is created, eliminating the identity race condition.
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

  # ── Key Vault secret references ───────────────────────────────────────────
  # Includes: SQL creds, Gusto client secret, Gusto webhook secret, AppInsights.
  # Values are never stored in Terraform state — fetched at container startup
  # via the managed identity.
  dynamic "secret" {
    for_each = { for ref in var.kv_secret_references : lower(ref.kv_secret_name) => ref }
    content {
      name                = secret.key
      key_vault_secret_id = "${trimsuffix(var.key_vault_uri, "/")}/secrets/${secret.value.kv_secret_name}"
      identity            = var.identity_id
    }
  }

  # ── HTTP ingress ──────────────────────────────────────────────────────────
  # external_enabled = true — AFD reaches this app via its public CAE hostname.
  # AFD terminates TLS; the broker receives plain HTTP from AFD on the target port.
  # No IP restrictions here — WAF on AFD is the perimeter control.
  ingress {
    external_enabled = true
    target_port      = var.container_port
    transport        = "http"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = var.app_name
      # Placeholder — GitHub Actions overwrites this on first deploy.
      image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
      cpu    = var.cpu
      memory = var.memory

      # ── Service Bus config (non-secret) ───────────────────────────────────
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

      # ── Identity + runtime config ─────────────────────────────────────────
      env {
        name  = "KEY_VAULT_URL"
        value = var.key_vault_uri
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      # Required by DefaultAzureCredential when multiple MIs are attached.
      env {
        name  = "AZURE_CLIENT_ID"
        value = var.identity_client_id
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = var.app_name
      }

      # ── Caller-supplied non-secret env vars ───────────────────────────────
      # e.g. PAYROLL_BROKER_BASE_URL — provider URLs now stored in payroll_providers DB row
      dynamic "env" {
        for_each = var.environment_variables
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      # ── KV-backed env vars ────────────────────────────────────────────────
      # References ACA secret names — actual values fetched from KV at startup.
      dynamic "env" {
        for_each = var.kv_secret_references
        content {
          name        = env.value.env_name
          secret_name = lower(env.value.kv_secret_name)
        }
      }
    }

    # ── KEDA — Azure Service Bus scaler ──────────────────────────────────────
    # Scales from min_replicas upward when messages accumulate in the
    # payroll-processor subscription. min_replicas = 1 ensures the HTTP
    # endpoint is always alive even when the queue is empty.
    custom_scale_rule {
      name             = "servicebus-scaler"
      custom_rule_type = "azure-servicebus"
      metadata = {
        namespace        = split(".", var.service_bus_fqns)[0]   # short name only
        topicName        = var.service_bus_topic_name
        subscriptionName = var.service_bus_subscription_name
        messageCount     = tostring(var.keda_message_count)
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
    ]
  }
}
