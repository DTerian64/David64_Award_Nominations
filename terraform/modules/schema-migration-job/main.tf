# modules/schema-migration-job/main.tf
# -----------------------------------------------------------------------------
# Schema migration Container Apps Job (ADR-0001).
#
# Runs `alembic upgrade head` from the schema-migration image, INSIDE the VNet
# (via the CAE), as a user-assigned Managed Identity that is a member of
# sql-migrations-<env> (db_ddladmin). It reaches the private-endpoint SQL server
# that GitHub-hosted runners cannot.
#
# Manual trigger only -- scale-to-zero. Nothing runs until:
#   az containerapp job start --name <job> --resource-group <rg>
# GitHub Actions builds/pushes the image, updates it here, then starts the job.
#
# The container image's ENTRYPOINT is `alembic` (CMD `upgrade head`), so no
# command override is needed. The placeholder image is replaced by CI on first
# deploy; ignore_changes keeps Terraform from reverting it.
# -----------------------------------------------------------------------------
resource "azurerm_container_app_job" "schema_migration" {
  name                         = var.job_name
  resource_group_name          = var.resource_group_name
  location                     = var.location
  container_app_environment_id = var.container_app_environment_id
  tags                         = var.tags

  replica_timeout_in_seconds = var.replica_timeout_in_seconds
  replica_retry_limit        = 0 # fail fast -- a failed migration must not silently retry

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
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

  template {
    container {
      name   = var.job_name
      image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest" # placeholder; CI overwrites
      cpu    = var.cpu
      memory = var.memory

      # env.py: no SQL_USER/PASSWORD => DefaultAzureCredential; AZURE_CLIENT_ID
      # selects this user-assigned MI for the Entra token.
      env {
        name  = "MI_CLIENT_ID"
        value = var.identity_client_id
      }
      env {
        name  = "SQL_SERVER"
        value = var.sql_server_fqdn
      }
      env {
        name  = "SQL_DATABASE"
        value = var.sql_database_name
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = var.job_name
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}
