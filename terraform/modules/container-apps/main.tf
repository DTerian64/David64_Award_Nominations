# modules/container-apps/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Container App Environments + Container Apps
#
# Creates:
#   - CAE Primary region (VNet injected into subnet-aca-primary)
#   - CAE Secondary region (VNet injected into subnet-aca-secondary)
#   - Container App primary region
#   - Container App secondary region
#   - System-assigned managed identity on each Container App
#
# IMPORTANT: Image tag is set to a placeholder on initial deploy.
# GitHub Actions overwrites the image on every push via:
#   az containerapp update --name ... --image ...
# lifecycle.ignore_changes on image prevents terraform apply from
# resetting the image back to the placeholder on subsequent runs.
# ─────────────────────────────────────────────────────────────────────────────

# ── Container App Environment — East US ───────────────────────────────────────
resource "azurerm_container_app_environment" "primary" {
  name                       = var.cae_name_primary
  resource_group_name        = var.resource_group_name
  location                   = var.location_primary
  log_analytics_workspace_id = var.log_analytics_workspace_primary_id

  # VNet injection — internal_load_balancer_enabled controls public vs private.
  # false (default) → public IP, reachable by Front Door Standard.
  # true            → private IP only, requires Front Door Premium + Private Link.
  infrastructure_subnet_id       = var.subnet_aca_primary_id
  internal_load_balancer_enabled = var.internal_load_balancer_enabled

  tags = var.tags

  lifecycle {
    # Azure auto-creates a managed resource group (ME_...) on first deploy and
    # records it in state. Ignoring it prevents Terraform from forcing a CAE
    # destroy/recreate on subsequent plans when it's not declared in config.
    ignore_changes = [infrastructure_resource_group_name]
  }
}

# ── Container App Environment — West US ───────────────────────────────────────
resource "azurerm_container_app_environment" "secondary" {
  name                       = var.cae_name_secondary
  resource_group_name        = var.resource_group_name
  location                   = var.location_secondary
  log_analytics_workspace_id = var.log_analytics_workspace_secondary_id

  infrastructure_subnet_id       = var.subnet_aca_secondary_id
  internal_load_balancer_enabled = var.internal_load_balancer_enabled

  tags = var.tags

  lifecycle {
    ignore_changes = [infrastructure_resource_group_name]
  }
}

# ── Container App — East US ───────────────────────────────────────────────────
resource "azurerm_container_app" "primary" {
  name                         = var.app_name_primary
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.primary.id
  revision_mode                = "Single"
  tags                         = var.tags

  # User-assigned managed identity — pre-authorized for KV access before this
  # Container App is created, eliminating the system-assigned identity race condition.
  identity {
    type         = "UserAssigned"
    identity_ids = [var.aca_primary_identity_id]
  }

  # Registry credentials for ACR image pull
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
  # The user-assigned managed identity is referenced by its full resource ID.
  # The actual secret value is never stored in Terraform state.
  dynamic "secret" {
    for_each = { for ref in var.kv_secret_references : lower(ref.kv_secret_name) => ref }
    content {
      name                = secret.key
      key_vault_secret_id = "${trimsuffix(var.key_vault_uri, "/")}/secrets/${secret.value.kv_secret_name}"
      identity            = var.aca_primary_identity_id
    }
  }

  ingress {
    external_enabled = true    # public — reachable by Front Door Standard
    target_port      = 8000
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
      name   = "award-api"
      # Placeholder image — GitHub Actions overwrites this on first deploy
      image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
      cpu    = var.cpu
      memory = var.memory

      # Non-secret config values — passed as plain env vars
      dynamic "env" {
        for_each = var.environment_variables
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      # KV-backed env vars — reference ACA secret names (not values directly)
      dynamic "env" {
        for_each = var.kv_secret_references
        content {
          name        = env.value.env_name
          secret_name = lower(env.value.kv_secret_name)
        }
      }
    }
  }

  lifecycle {
    # image is owned by GitHub Actions — never reset it on terraform apply.
    # secret is now fully managed by Terraform (ACR password + KV references).
    ignore_changes = [
      template[0].container[0].image,
    ]
  }
}

# ── Container App — West US ───────────────────────────────────────────────────
resource "azurerm_container_app" "secondary" {
  name                         = var.app_name_secondary
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.secondary.id
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.aca_secondary_identity_id]
  }

  registry {
    server               = var.acr_login_server
    username             = var.acr_admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_admin_password
  }

  dynamic "secret" {
    for_each = { for ref in var.kv_secret_references : lower(ref.kv_secret_name) => ref }
    content {
      name                = secret.key
      key_vault_secret_id = "${trimsuffix(var.key_vault_uri, "/")}/secrets/${secret.value.kv_secret_name}"
      identity            = var.aca_secondary_identity_id
    }
  }

  ingress {
    external_enabled = true    # public — reachable by Front Door Standard
    target_port      = 8000
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
      name   = "award-api"
      image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
      cpu    = var.cpu
      memory = var.memory

      dynamic "env" {
        for_each = var.environment_variables
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      dynamic "env" {
        for_each = var.kv_secret_references
        content {
          name        = env.value.env_name
          secret_name = lower(env.value.kv_secret_name)
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
