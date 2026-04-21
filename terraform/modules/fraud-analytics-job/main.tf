# modules/fraud-analytics-job/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Fraud Analytics Container Apps Job
#
# Runs two scripts in sequence on a weekly cron schedule:
#   1. train_fraud_model.py   — per-tenant Random Forest retrain; upserts scores
#                               into dbo.FraudScores; uploads .pkl to Blob Storage.
#   2. graph_pattern_detector.py — Azure SQL Graph MATCH queries + networkx + NLP;
#                               writes behavioural pattern findings to
#                               dbo.GraphPatternFindings.
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
#   2 vCPU / 4 Gi — scikit-learn model training + graph MATCH queries + NLP
#   inference can peak at ~3 Gi on 13 000 nominations. 4 Gi gives headroom.
#   replica_timeout_in_seconds = 3600 — both scripts together finish well
#   under 10 min; 1 hour is a generous ceiling for data growth.
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
  tags                         = var.tags

  # ── Trigger — weekly cron; job is also always manually startable ─────────
  replica_timeout_in_seconds = 7200  # 2-hour ceiling: up to 60 min image pull on cold node + ~10 min actual run
  replica_retry_limit        = 1     # fail fast — alert, don't silently retry

  schedule_trigger_config {
    cron_expression          = var.cron_expression   # default: "0 2 * * 1"
    parallelism              = 1
    replica_completion_count = 1
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
  # Convention: kv_secret_name UPPER-HYPHEN → ACA secret name lower-hyphen.
  dynamic "secret" {
    for_each = { for ref in var.kv_secret_references : lower(ref.kv_secret_name) => ref }
    content {
      name                = secret.key
      key_vault_secret_id = "${trimsuffix(var.key_vault_uri, "/")}/secrets/${secret.value.kv_secret_name}"
      identity            = var.analytics_identity_id
    }
  }

  template {
    container {
      name  = var.job_name
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
        name  = "AZURE_CLIENT_ID"
        value = var.analytics_identity_client_id
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
          secret_name = lower(env.value.kv_secret_name)
        }
      }
    }
  }

  lifecycle {
    # Image tag is owned by GitHub Actions — never reset on terraform apply.
    ignore_changes = [
      template[0].container[0].image,
    ]
  }
}
