# modules/fraud-analytics-job/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Fraud Analytics Container Apps Job
#
# Runs the coordinated integrity analytics pipeline on a weekly cron schedule.
# Global preparation runs once per execution, then enabled tenants are claimed
# from the SQL-backed queue and processed through Graph, Tabular, GNN, and
# Forecast stages. Multiple replicas share the same execution ledger.
#
# Trigger model:
#   - Scheduled: cron "0 2 * * 1" — Monday 02:00 UTC every week.
#   - On-demand:  az containerapp job start --name <job> --resource-group <rg>
#     Jobs with schedule trigger can always be started manually via CLI/portal —
#     no separate manual trigger configuration required.
#
# Authentication model (mirrors auxiliary-container-app pattern):
#   A User-Assigned Managed Identity (pre-created in the environment main.tf)
#   is used for:
#     - Key Vault secret resolution (SQL credentials, Storage key, AppInsights)
#     - Azure Blob Storage access for .pkl model upload/download
#     - Azure SQL access (MI must be added as a SQL contained user by DBA)
#   No connection strings or SAS tokens — all access via MI.
#
# Sizing:
#   4 vCPU / 8 Gi per replica — the GNN stage is the current peak workload.
#   Timeout, retry, parallelism, and required completions are caller-controlled.
#
# Lifecycle note:
#   The image tag is managed by GitHub Actions (not Terraform). A placeholder
#   image is used on first apply; ignore_changes prevents subsequent applies
#   from reverting it to the placeholder.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_container_app_job" "fraud_analytics" {
  name                         = var.job_name
  resource_group_name          = var.resource_group_name
  location                     = var.location
  container_app_environment_id = var.container_app_environment_id
  workload_profile_name        = var.workload_profile_name
  tags                         = var.tags

  # ── Trigger — weekly cron; job is also always manually startable ─────────
  replica_timeout_in_seconds = var.replica_timeout_in_seconds
  replica_retry_limit        = var.replica_retry_limit

  schedule_trigger_config {
    cron_expression          = var.cron_expression # default: "0 2 * * 1"
    parallelism              = var.parallelism
    replica_completion_count = var.replica_completion_count
  }

  # ── Identity — User-Assigned MI (pre-created before KV access policy) ─────
  identity {
    type         = "UserAssigned"
    identity_ids = [var.analytics_identity_id]
  }

  # ── ACR — image pull credentials ──────────────────────────────────────────
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
  # Each ACA secret resolves its value from KV at job startup via the MI.
  # The actual secret value never appears in Terraform state.
  # Convention: env_name UPPER_UNDERSCORE → ACA secret name lower-hyphen.
  dynamic "secret" {
    # Key by the logical environment variable, not by Key Vault secret name.
    # Award and Integrity Sentinel connections intentionally share secrets
    # during the one-database transition and therefore duplicate KV names.
    for_each = {
      for ref in var.kv_secret_references :
      lower(replace(ref.env_name, "_", "-")) => ref
    }
    content {
      name                = secret.key
      key_vault_secret_id = "${trimsuffix(var.key_vault_uri, "/")}/secrets/${secret.value.kv_secret_name}"
      identity            = var.analytics_identity_id
    }
  }

  template {
    container {
      name = var.job_name
      # Placeholder image — GitHub Actions overwrites this on first deploy.
      image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
      cpu    = var.cpu
      memory = var.memory

      # ── Non-secret env vars ───────────────────────────────────────────────
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "AZURE_STORAGE_ACCOUNT"
        value = var.storage_account_name
      }
      env {
        name  = "MODEL_CONTAINER"
        value = var.model_container_name
      }
      env {
        name  = "MI_CLIENT_ID"
        value = var.analytics_identity_client_id
      }
      # Thread cap for torch / OpenMP.
      #
      # Without this, torch reads the HOST's core count rather than the cgroup
      # quota and spawns that many OpenMP threads inside a 4-vCPU container.
      # The result is oversubscription: more context switching than compute, and
      # a GNN training stage that gets slower as the node gets bigger. Tied to
      # var.cpu so the two can never drift apart.
      env {
        name  = "OMP_NUM_THREADS"
        value = tostring(var.cpu)
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = var.job_name
      }

      # ── Caller-supplied non-secret env vars ───────────────────────────────
      dynamic "env" {
        for_each = var.environment_variables
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      # ── KV-backed env vars ────────────────────────────────────────────────
      dynamic "env" {
        for_each = var.kv_secret_references
        content {
          name        = env.value.env_name
          secret_name = lower(replace(env.value.env_name, "_", "-"))
        }
      }
    }
  }

  lifecycle {
    # Image tag is owned by GitHub Actions — never reset on terraform apply.
    ignore_changes = [
      template[0].container[0].image,
    ]

    precondition {
      condition     = var.replica_completion_count <= var.parallelism
      error_message = "replica_completion_count cannot exceed parallelism."
    }
  }
}
