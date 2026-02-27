# modules/container-apps/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Container App Environments + Container Apps
#
# Creates:
#   - CAE East US (internal, VNet injected into subnet-aca-eastus)
#   - CAE West US (internal, VNet injected into subnet-aca-westus)
#   - Container App East US (award-api-eastus)
#   - Container App West US (award-api-westus)
#   - System-assigned managed identity on each Container App
#
# IMPORTANT: Image tag is set to a placeholder on initial deploy.
# GitHub Actions overwrites the image on every push via:
#   az containerapp update --name ... --image ...
# lifecycle.ignore_changes on image prevents terraform apply from
# resetting the image back to the placeholder on subsequent runs.
# ─────────────────────────────────────────────────────────────────────────────

# ── Container App Environment — East US ───────────────────────────────────────
resource "azurerm_container_app_environment" "east" {
  name                       = var.cae_name_east
  resource_group_name        = var.resource_group_name
  location                   = var.location_east
  log_analytics_workspace_id = var.log_analytics_workspace_east_id

  # VNet injection — makes CAE internal only
  infrastructure_subnet_id       = var.subnet_aca_east_id
  internal_load_balancer_enabled = true

  tags = var.tags
}

# ── Container App Environment — West US ───────────────────────────────────────
resource "azurerm_container_app_environment" "west" {
  name                       = var.cae_name_west
  resource_group_name        = var.resource_group_name
  location                   = var.location_west
  log_analytics_workspace_id = var.log_analytics_workspace_west_id

  infrastructure_subnet_id       = var.subnet_aca_west_id
  internal_load_balancer_enabled = true

  tags = var.tags
}

# ── Container App — East US ───────────────────────────────────────────────────
resource "azurerm_container_app" "east" {
  name                         = var.app_name_east
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.east.id
  revision_mode                = "Single"
  tags                         = var.tags

  # System-assigned managed identity — used for KV access
  identity {
    type = "SystemAssigned"
  }

  # Registry credentials for ACR image pull
  registry {
    server               = var.acr_login_server
    username             = var.acr_admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_admin_password
  }

  ingress {
    external_enabled = false   # internal only — AFD connects via Private Link
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

      # Environment variables — wired from other module outputs
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
    # Never let terraform overwrite the image — GitHub Actions owns this
    ignore_changes = [
      template[0].container[0].image,
      secret,
    ]
  }
}

# ── Container App — West US ───────────────────────────────────────────────────
resource "azurerm_container_app" "west" {
  name                         = var.app_name_west
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.west.id
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type = "SystemAssigned"
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

  ingress {
    external_enabled = false
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
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
      secret,
    ]
  }
}
