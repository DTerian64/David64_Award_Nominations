# modules/auxiliary-container-app/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Award Auxiliary Container App — event-driven worker (no HTTP ingress)
#
# Creates a Container App worker that:
#   - Consumes messages from the award-events Service Bus topic
#   - Scales to zero via KEDA when the subscription is empty
#   - Dispatches to handlers based on event type (email, exports, HR sync, etc.)
#   - Reads secrets at runtime from Key Vault via Managed Identity
#
# Authentication model:
#   The container app uses a User-Assigned Managed Identity (pre-created in the
#   environment main.tf before this module runs, following the same pattern as
#   the API Container Apps). This identity is:
#     - Granted Azure Service Bus Data Receiver on the award-events topic
#     - Granted Key Vault secret Get/List via an access policy
#     - Referenced by KEDA via clientId for workload identity auth
#
# KEDA scaler:
#   azure-servicebus scaler type; scales on message count in the subscription.
#   fullyQualifiedNamespace + clientId → KEDA authenticates via workload identity
#   (the managed identity) — no connection strings or SAS tokens required.
#
# No ingress:
#   Worker containers do not expose HTTP endpoints. Omitting the ingress block
#   entirely prevents Azure from creating a load balancer or public IP.
#   The container is purely pull-based (Service Bus → KEDA → scale).
#
# Lifecycle note:
#   The image tag is managed by GitHub Actions (not Terraform). A placeholder
#   image is used on first apply; ignore_changes prevents subsequent applies
#   from resetting the image back to the placeholder.
#
# Extending to new event types:
#   Add a new handler in the auxiliary service code and register it in the
#   dispatcher. No Terraform changes required — the same container handles
#   all event types for this subscription.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_container_app" "auxiliary" {
  name                         = var.app_name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.container_app_environment_id
  revision_mode                = "Single"
  tags                         = var.tags

  # User-assigned managed identity — pre-authorized for KV and Service Bus
  # before this resource is created, eliminating the identity race condition.
  identity {
    type         = "UserAssigned"
    identity_ids = [var.auxiliary_identity_id]
  }

  # ACR credentials for image pull
  registry {
    server               = var.acr_login_server
    username             = var.acr_admin_username
    password_secret_name = "acr-password"
  }

  # ── ACR password secret ───────────────────────────────────────────────────
  secret {
    name  = "acr-password"
    value = var.acr_admin_password
  }

  # ── Key Vault secret references ───────────────────────────────────────────
  # Each entry creates an ACA secret that resolves its value from KV at runtime.
  # The managed identity is referenced by its full resource ID.
  # The actual secret value is never stored in Terraform state.
  # Convention: kv_secret_name uses UPPER-HYPHEN (e.g. "SQL-PASSWORD")
  #             ACA secret name derived as: lower(kv_secret_name)
  dynamic "secret" {
    for_each = { for ref in var.kv_secret_references : lower(ref.kv_secret_name) => ref }
    content {
      name                = secret.key
      key_vault_secret_id = "${trimsuffix(var.key_vault_uri, "/")}/secrets/${secret.value.kv_secret_name}"
      identity            = var.auxiliary_identity_id
    }
  }

  # ── NO ingress block ──────────────────────────────────────────────────────
  # This is a pure worker (pull-based). No HTTP server, no load balancer,
  # no public IP. KEDA wakes the container when messages arrive.

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name  = var.app_name
      # Placeholder image — GitHub Actions overwrites this on first deploy.
      image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
      cpu    = var.cpu
      memory = var.memory

      # ── Built-in non-secret env vars ─────────────────────────────────────
      # Service Bus — FQNS is not sensitive; the MI credential grants access.
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

      # Key Vault URL — tells the app where to resolve secrets at startup.
      env {
        name  = "KEY_VAULT_URL"
        value = var.key_vault_uri
      }

      # ENVIRONMENT — used for logging, feature flags, and conditional logic.
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }

      # AZURE_CLIENT_ID — required by DefaultAzureCredential when multiple
      # managed identities are attached to the same compute resource.
      # Without this, the SDK cannot determine which MI to use.
      env {
        name  = "AZURE_CLIENT_ID"
        value = var.auxiliary_identity_client_id
      }

      # OTEL service name — identifies this worker in Application Insights.
      env {
        name  = "OTEL_SERVICE_NAME"
        value = var.app_name
      }

      # ── Caller-supplied non-secret env vars ───────────────────────────────
      # e.g. AZURE_STORAGE_ACCOUNT, MODEL_CONTAINER, etc.
      dynamic "env" {
        for_each = var.environment_variables
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      # ── KV-backed env vars ────────────────────────────────────────────────
      # Reference ACA secret names (derived from kv_secret_name) — not values.
      # The actual secret is fetched from KV at container startup.
      dynamic "env" {
        for_each = var.kv_secret_references
        content {
          name        = env.value.env_name
          secret_name = lower(env.value.kv_secret_name)
        }
      }
    }

    # ── KEDA — Azure Service Bus scaler ──────────────────────────────────────
    # Scales the worker based on the number of active messages in the subscription.
    # Authentication uses the Container App's managed identity automatically —
    # ACA's embedded KEDA picks up the single user-assigned MI without explicit
    # auth configuration.
    #
    # ACA metadata for the azure-servicebus KEDA scaler.
    # Authentication uses the container app's user-assigned managed identity
    # (workload identity) — no connection string required.
    #
    # clientId is REQUIRED when using a user-assigned managed identity so
    # ACA's embedded KEDA knows which identity to use. Without it KEDA falls
    # back to no authentication and raises "no connection setting given".
    #
    # namespace: short name only (no .servicebus.windows.net).
    # messageCount: target messages per replica. One replica is activated when
    # pending messages >= this value; scales to zero at zero messages.
    custom_scale_rule {
      name             = "servicebus-scaler"
      custom_rule_type = "azure-servicebus"
      metadata = {
        # Extract namespace name from FQNS: "sb-award-sandbox.servicebus.windows.net" → "sb-award-sandbox"
        namespace        = split(".", var.service_bus_fqns)[0]
        topicName        = var.service_bus_topic_name
        subscriptionName = var.service_bus_subscription_name
        messageCount     = tostring(var.keda_message_count)
        # Required for user-assigned managed identity workload auth
        clientId         = var.auxiliary_identity_client_id
      }
    }
  }

  lifecycle {
    # image is owned by GitHub Actions — never reset it on terraform apply.
    ignore_changes = [
      template[0].container[0].image,
    ]
  }
}
