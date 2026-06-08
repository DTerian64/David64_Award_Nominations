# environments/dev/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Sandbox environment — wires all modules together
# App registrations are CREATED by Terraform for sandbox
# ─────────────────────────────────────────────────────────────────────────────

data "azurerm_client_config" "current" {}

locals {
  tags = {
    environment = var.environment
    project     = "award-nomination"
    managed_by  = "terraform"
  }
}

# ── 0. Resource Group ─────────────────────────────────────────────────────────
resource "azurerm_resource_group" "rg" {
  name     = var.resource_group_name
  location = var.location_primary
  tags     = local.tags

  lifecycle {
    # RG location is metadata only — it does not control where resources are deployed.
    # Prevent Terraform from destroying/recreating the RG if location_primary changes.
    ignore_changes = [location]
  }
}

# ── Azure AD — create new app registrations for dev ───────────────────────────
module "app_registrations" {
  source                = "../../modules/app-registrations"
  environment           = var.environment
  swa_urls              = var.swa_redirect_urls
  admin_user_object_ids = var.admin_user_object_ids
  admin_app_role_id     = var.admin_app_role_id
}

# ── 1. Networking ─────────────────────────────────────────────────────────────
module "networking" {
  source = "../../modules/networking"

  resource_group_name          = var.resource_group_name
  environment                  = var.environment
  location_primary             = var.location_primary
  location_secondary           = var.location_secondary
  vnet_primary_address_space   = "10.2.0.0/16"
  vnet_secondary_address_space = "10.3.0.0/16"
  tags                         = local.tags
  depends_on                   = [azurerm_resource_group.rg]
}

# ── 2. SQL ────────────────────────────────────────────────────────────────────
module "sql" {
  source = "../../modules/sql"

  resource_group_name        = var.resource_group_name
  location                   = var.sql_location              # westus2 — SQL provisioning restricted in eastus/eastus2
  private_endpoint_location  = var.location_primary          # eastus2 — PE NIC must match subnet region
  server_name                = var.sql_server_name
  database_name              = var.sql_database_name
  admin_login                = var.sql_admin_login
  admin_password             = var.sql_admin_password
  allowed_ips                = var.my_ips
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_sql_id
  tags                       = local.tags
  depends_on                 = [azurerm_resource_group.rg, module.networking]
}

# ── 3. Container Registry ─────────────────────────────────────────────────────
module "container_registry" {
  source = "../../modules/container-registry"

  resource_group_name        = var.resource_group_name
  location                   = var.location_primary
  acr_name                   = var.acr_name
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_acr_id
  tags                       = local.tags
  depends_on                 = [azurerm_resource_group.rg, module.networking]
}

# ── 4. Storage ────────────────────────────────────────────────────────────────
module "storage" {
  source = "../../modules/storage"

  resource_group_name        = var.resource_group_name
  location                   = var.location_primary
  storage_account_name       = var.storage_account_name
  allowed_ips                = var.my_ips
  # Only the primary (westus2) ACA subnet — Azure blocks cross-region service-endpoint ACLs.
  # Secondary ACA (eastus) reaches storage via VNet peering → private endpoint.
  aca_subnet_ids             = [module.networking.subnet_aca_primary_id]
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_blob_id
  tags                       = local.tags
  depends_on                 = [azurerm_resource_group.rg, module.networking]
}

# ── 4b. User-Assigned Managed Identities ─────────────────────────────────────
# Created BEFORE Key Vault access policies and Container Apps.
# This eliminates the system-assigned identity race condition where Azure tries to
# validate KV-backed secrets before the access policy for the new identity exists.
# Dependency order: MI → KV access policy → resource (with KV secrets)
resource "azurerm_user_assigned_identity" "aca_primary" {
  name                = "id-award-api-primary-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location_primary
  tags                = local.tags
  depends_on          = [azurerm_resource_group.rg]
}

resource "azurerm_user_assigned_identity" "aca_secondary" {
  name                = "id-award-api-secondary-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location_secondary
  tags                = local.tags
  depends_on          = [azurerm_resource_group.rg]
}

# Auxiliary Function identity — created here (before KV) so the KV access policy
# and Service Bus RBAC assignments can be granted before the Function App is created.
resource "azurerm_user_assigned_identity" "auxiliary_function" {
  name                = "id-award-auxiliary-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location_primary
  tags                = local.tags
  depends_on          = [azurerm_resource_group.rg]
}

# Fraud Analytics Job identity — pre-created so the KV access policy can be
# granted before the Container Apps Job is provisioned (same race-avoidance
# pattern as the API and auxiliary identities above).
resource "azurerm_user_assigned_identity" "fraud_analytics_job" {
  name                = "id-award-fraud-analytics-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location_primary
  tags                = local.tags
  depends_on          = [azurerm_resource_group.rg]
}

# Integrity Check identity — pre-created so KV access policy and Service Bus /
# Blob RBAC assignments can be granted before the Container App is created.
# This container runs fraud_check.py: streams pkl from Blob, writes to SQL,
# re-publishes events to Service Bus.
resource "azurerm_user_assigned_identity" "integrity_check" {
  name                = "id-award-integrity-check-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location_primary
  tags                = local.tags
  depends_on          = [azurerm_resource_group.rg]
}

# ── 5. Key Vault ──────────────────────────────────────────────────────────────
module "key_vault" {
  source = "../../modules/key-vault"

  resource_group_name        = var.resource_group_name
  location                   = var.location_primary
  key_vault_name             = var.key_vault_name
  allowed_ips                = var.my_ips
  # Only the primary (westus2) ACA subnet — same cross-region ACL restriction as storage.
  aca_subnet_ids             = [module.networking.subnet_aca_primary_id]
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_kv_id
  aca_principal_ids          = []
  tags                       = local.tags
  depends_on                 = [azurerm_resource_group.rg, module.networking]

  # var.secrets (from terraform.tfvars) supplies: SQL-USER, SQL-PASSWORD, GMAIL-APP-PASSWORD
  # Remaining secrets are derived from other module outputs so they stay in sync automatically.
  secrets = merge(var.secrets, {
    AZURE-STORAGE-KEY                     = module.storage.primary_access_key
    AZURE-OPENAI-KEY                      = module.openai.primary_access_key
    AZURE-OPENAI-ENDPOINT                 = module.openai.endpoint
    SQL-SERVER                            = module.sql.server_fqdn
    SQL-DATABASE                          = module.sql.database_name
    APPINSIGHTS-CONNECTION-STRING-BACKEND = module.application_insights.backend_connection_string
    # Shared secret — Award API validates this on inbound webhook calls from
    # Workday_Proxy (sandbox) or real Workday (prod). X-Api-Key header.
    # Same value must be in Workday_proxy/terraform/.../terraform.tfvars → workday_webhook_secret.
    WORKDAY-WEBHOOK-SECRET                = var.workday_webhook_secret
    # Shared secret — Award API validates this on the internal POST
    # /api/internal/refresh-fraud-model callback from the fraud-analytics-job.
    # Same value must be set in fraud-analytics-job terraform → fraud_analytics_job_webhook_secret.
    FRAUD-ANALYTICS-JOB-WEBHOOK-SECRET = var.fraud_analytics_job_webhook_secret
    # Shared secret — Award API validates this on the daily POST
    # /api/internal/checkPendingHRBPReview callback from la-award-hrbp-sla.
    HRBP-SLA-WEBHOOK-SECRET            = var.hrbp_sla_webhook_secret
  })
}

# ── 6. OpenAI ─────────────────────────────────────────────────────────────────
module "openai" {
  source = "../../modules/openai"

  resource_group_name        = var.resource_group_name
  location                   = var.location_primary
  openai_name                = var.openai_name
  model_capacity_tpm         = var.model_capacity_tpm
  allowed_ips                = var.my_ips
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_openai_id
  tags                       = local.tags
  depends_on                 = [azurerm_resource_group.rg, module.networking]
}

# ── 7. Log Analytics ──────────────────────────────────────────────────────────
module "log_analytics" {
  source = "../../modules/log-analytics"

  resource_group_name      = var.resource_group_name
  location_primary         = var.location_primary
  location_secondary       = var.location_secondary
  workspace_name_primary   = var.workspace_name_primary
  workspace_name_secondary = var.workspace_name_secondary
  tags                     = local.tags
  depends_on               = [azurerm_resource_group.rg]
}

# ── 7b. Application Insights ──────────────────────────────────────────────────
module "application_insights" {
  source = "../../modules/application-insights"

  resource_group_name        = var.resource_group_name
  location                   = var.location_primary
  environment                = var.environment
  log_analytics_workspace_id = module.log_analytics.workspace_primary_id
  tags                       = local.tags
  depends_on                 = [azurerm_resource_group.rg, module.log_analytics]
}

# ── 8. Container Apps ─────────────────────────────────────────────────────────
module "container_apps" {
  source = "../../modules/container-apps"

  resource_group_name                  = var.resource_group_name
  location_primary                     = var.location_primary
  location_secondary                   = var.location_secondary
  cae_name_primary                     = var.cae_name_primary
  cae_name_secondary                   = var.cae_name_secondary
  app_name_primary                     = var.app_name_primary
  app_name_secondary                   = var.app_name_secondary
  subnet_aca_primary_id                = module.networking.subnet_aca_primary_id
  subnet_aca_secondary_id              = module.networking.subnet_aca_secondary_id
  min_replicas                       = var.min_replicas
  max_replicas                       = var.max_replicas
  log_analytics_workspace_primary_id   = module.log_analytics.workspace_primary_id
  log_analytics_workspace_secondary_id = module.log_analytics.workspace_secondary_id
  acr_login_server                   = module.container_registry.login_server
  acr_admin_username                 = module.container_registry.admin_username
  acr_admin_password                 = module.container_registry.admin_password
  key_vault_uri                      = module.key_vault.vault_uri
  aca_primary_identity_id            = azurerm_user_assigned_identity.aca_primary.id
  aca_primary_identity_client_id     = azurerm_user_assigned_identity.aca_primary.client_id
  aca_secondary_identity_id          = azurerm_user_assigned_identity.aca_secondary.id
  aca_secondary_identity_client_id   = azurerm_user_assigned_identity.aca_secondary.client_id
  # KV access policies and Service Bus RBAC must exist before Container Apps start.
  depends_on                      = [azurerm_resource_group.rg, module.key_vault,
                                     azurerm_key_vault_access_policy.aca_primary,
                                     azurerm_key_vault_access_policy.aca_secondary,
                                     module.service_bus]

  # Non-secret config — passed as plain env vars
  environment_variables = [
    { name = "AZURE_STORAGE_ACCOUNT",           value = module.storage.storage_account_name },
    { name = "MODEL_CONTAINER",                 value = module.storage.ml_models_container_name },
    { name = "EXTRACTS_CONTAINER",              value = module.storage.extracts_container_name },
    { name = "AZURE_OPENAI_MODEL",              value = module.openai.model_deployment_name },
    { name = "KEY_VAULT_URL",                   value = module.key_vault.vault_uri },
    { name = "ENVIRONMENT",                     value = var.environment },
    { name = "REGION",                          value = var.location_primary },
    { name = "CONTAINER_APP_NAME",              value = var.app_name_primary },
    { name = "AZURE_OPENAI_API_VERSION",        value = var.openai_api_version },
    { name = "MODEL_BLOB_NAME",                 value = var.model_blob_name },
    { name = "API_BASE_URL",                    value = var.api_base_url },
    { name = "CORS_ALLOWED_ORIGINS",            value = var.cors_allowed_origins },
    { name = "LOGGING_LEVEL",                   value = var.logging_level },
    { name = "BLOB_SAS_EXPIRY_HOURS",           value = tostring(var.blob_sas_expiry_hours) },
    # CLIENT_ID is required by auth.py for JWT audience validation (api://<client_id>).
    { name = "CLIENT_ID",                       value = module.app_registrations.api_client_id },
    # Service Bus — neither FQNS nor topic name is sensitive; MI credential grants access.
    { name = "SERVICE_BUS_FQNS",                value = module.service_bus.namespace_fqns },
    { name = "SERVICE_BUS_TOPIC_NAME",          value = module.service_bus.topic_name },
    # Fraud model lazy-load tuning — shorter than prod so eviction is observable in dev.
    { name = "MODEL_IDLE_TTL_SECONDS",          value = tostring(var.model_idle_ttl_seconds) },
    { name = "MODEL_EVICTION_INTERVAL_SECONDS", value = tostring(var.model_eviction_interval_seconds) },
    # Demo tenant self-registration — non-sensitive IDs; secret goes via Key Vault below
    { name = "DEMO_AAD_TENANT_ID",              value = var.demo_aad_tenant_id },
    { name = "DEMO_GRAPH_CLIENT_ID",            value = var.demo_graph_client_id },
    # Owner/developer test accounts that bypass the personal-email domain block
    { name = "DEMO_ALLOWED_EMAILS",             value = var.demo_allowed_emails },
    # HRBP SLA — hours before a PendingHRBPReview nomination triggers an escalation email
    { name = "HRBP_SLA_HOURS",                  value = tostring(var.hrbp_sla_hours) },
    # Log Analytics workspace GUID — used by the admin nomination-logs endpoint (azure-monitor-query).
    # Must be the customer/workspace GUID, NOT the ARM resource ID.
    { name = "LOG_ANALYTICS_WORKSPACE_ID",       value = module.log_analytics.workspace_primary_customer_id },
  ]

  # Secret config — fetched from Key Vault at runtime via managed identity
  # KV secret name convention: UPPER-HYPHEN (e.g. "SQL-PASSWORD")
  # ACA secret name derived as: lower(kv_secret_name) (e.g. "sql-password")
  kv_secret_references = [
    { env_name = "SQL_SERVER",          kv_secret_name = "SQL-SERVER" },
    { env_name = "SQL_DATABASE",        kv_secret_name = "SQL-DATABASE" },
    { env_name = "SQL_USER",            kv_secret_name = "SQL-USER" },
    { env_name = "SQL_PASSWORD",        kv_secret_name = "SQL-PASSWORD" },
    { env_name = "AZURE_STORAGE_KEY",   kv_secret_name = "AZURE-STORAGE-KEY" },
    { env_name = "GMAIL_APP_PASSWORD",  kv_secret_name = "GMAIL-APP-PASSWORD" },
    { env_name = "EMAIL_ACTION_SECRET_KEY",                  kv_secret_name = "EMAIL-ACTION-SECRET-KEY" },
    { env_name = "AZURE_OPENAI_KEY",                         kv_secret_name = "AZURE-OPENAI-KEY" },
    { env_name = "AZURE_OPENAI_ENDPOINT",                    kv_secret_name = "AZURE-OPENAI-ENDPOINT" },
    { env_name = "APPLICATIONINSIGHTS_CONNECTION_STRING",    kv_secret_name = "APPINSIGHTS-CONNECTION-STRING-BACKEND" },
    # Validates inbound webhook calls from Workday_Proxy (sandbox) or real Workday (prod).
    { env_name = "WORKDAY_WEBHOOK_SECRET",                   kv_secret_name = "WORKDAY-WEBHOOK-SECRET" },
    # Validates the post-training cache-refresh callback from the fraud-analytics-job.
    { env_name = "FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET", kv_secret_name = "FRAUD-ANALYTICS-JOB-WEBHOOK-SECRET" },
    # Validates the daily SLA-check callback from la-award-hrbp-sla Logic App.
    { env_name = "HRBP_SLA_WEBHOOK_SECRET",            kv_secret_name = "HRBP-SLA-WEBHOOK-SECRET" },
    # Demo tenant — Graph API client secret for self-registration (demo_router.py / graph_admin.py)
    { env_name = "DEMO_GRAPH_CLIENT_SECRET",           kv_secret_name = "DEMO-GRAPH-CLIENT-SECRET" },
  ]

  tags = local.tags
}

# ── Key Vault access policies for Container Apps ──────────────────────────────
# Reference the user-assigned MIs (created above) — not the Container Apps.
# This breaks the ordering race: MI exists → KV policy granted → Container App
# created with identity already authorized. No more 5s timeout errors.
resource "azurerm_key_vault_access_policy" "aca_primary" {
  key_vault_id = module.key_vault.key_vault_id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.aca_primary.principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [module.key_vault, azurerm_user_assigned_identity.aca_primary]
}

resource "azurerm_key_vault_access_policy" "aca_secondary" {
  key_vault_id = module.key_vault.key_vault_id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.aca_secondary.principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [module.key_vault, azurerm_user_assigned_identity.aca_secondary]
}

# KV access policy — Auxiliary Function
resource "azurerm_key_vault_access_policy" "auxiliary_function" {
  key_vault_id = module.key_vault.key_vault_id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.auxiliary_function.principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [module.key_vault, azurerm_user_assigned_identity.auxiliary_function]
}

# KV access policy — Fraud Analytics Job
resource "azurerm_key_vault_access_policy" "fraud_analytics_job" {
  key_vault_id = module.key_vault.key_vault_id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.fraud_analytics_job.principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [module.key_vault, azurerm_user_assigned_identity.fraud_analytics_job]
}

# KV access policy — Integrity Check container
resource "azurerm_key_vault_access_policy" "integrity_check" {
  key_vault_id = module.key_vault.key_vault_id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.integrity_check.principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [module.key_vault, azurerm_user_assigned_identity.integrity_check]
}

# Log Analytics Reader — both backend containers query Log Analytics for the admin
# nomination-logs endpoint (/api/admin/nominations/{id}/logs).
# Both primary and secondary need the role — Front Door load-balances between them
# and either container may handle the request.
# Requires Microsoft.OperationalInsights/workspaces/query/*/read — Log Analytics
# Reader grants this; Monitoring Reader does not.
resource "azurerm_role_assignment" "aca_primary_log_analytics_reader" {
  scope                = module.log_analytics.workspace_primary_id
  role_definition_name = "Log Analytics Reader"
  principal_id         = azurerm_user_assigned_identity.aca_primary.principal_id
  depends_on           = [azurerm_user_assigned_identity.aca_primary, module.log_analytics]
}

resource "azurerm_role_assignment" "aca_secondary_log_analytics_reader" {
  scope                = module.log_analytics.workspace_primary_id
  role_definition_name = "Log Analytics Reader"
  principal_id         = azurerm_user_assigned_identity.aca_secondary.principal_id
  depends_on           = [azurerm_user_assigned_identity.aca_secondary, module.log_analytics]
}

# Blob Storage reader — Integrity Check needs to stream pkl files from ml-models
resource "azurerm_role_assignment" "integrity_check_blob_reader" {
  scope                = module.storage.storage_account_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.integrity_check.principal_id
  depends_on           = [azurerm_user_assigned_identity.integrity_check, module.storage]
}

# Service Bus Data Sender — Integrity Check re-publishes nomination.created /
# nomination.fraud-flagged back to the topic after fraud assessment completes.
resource "azurerm_role_assignment" "integrity_check_sb_sender" {
  scope                = module.service_bus.topic_id
  role_definition_name = "Azure Service Bus Data Sender"
  principal_id         = azurerm_user_assigned_identity.integrity_check.principal_id
  depends_on           = [azurerm_user_assigned_identity.integrity_check, module.service_bus]
}

# ── 9. Service Bus ────────────────────────────────────────────────────────────
module "service_bus" {
  source = "../../modules/service-bus"

  resource_group_name = var.resource_group_name
  location            = var.location_primary
  namespace_name      = var.service_bus_namespace_name
  sku                 = "Standard"
  max_delivery_count  = 5
  tags                = local.tags

  # Static string keys let Terraform plan for_each even when principal IDs are unknown.
  sender_principal_ids = {
    "aca-primary"   = azurerm_user_assigned_identity.aca_primary.principal_id
    "aca-secondary" = azurerm_user_assigned_identity.aca_secondary.principal_id
  }

  receiver_principal_ids = {
    # auxiliary-function consumes email, payout, hrbp, and notification events.
    "auxiliary-function" = azurerm_user_assigned_identity.auxiliary_function.principal_id
    # integrity-check consumes nomination.submitted for async fraud detection.
    "integrity-check"    = azurerm_user_assigned_identity.integrity_check.principal_id
  }

  depends_on = [azurerm_resource_group.rg]
}

# ── 10. Auxiliary Container App ───────────────────────────────────────────────
# Event-driven worker: Service Bus → KEDA → container (no HTTP ingress).
# Scales to zero when idle; activates when messages arrive on award-events.
# Generic dispatcher handles all event types: email, exports, HR sync, etc.
module "auxiliary" {
  source = "../../modules/auxiliary-container-app"

  resource_group_name          = var.resource_group_name
  location                     = var.location_primary
  app_name                     = var.auxiliary_container_app_name
  environment                  = var.environment
  container_app_environment_id = module.container_apps.cae_primary_id

  # User-assigned identity — pre-authorized for KV and Service Bus above
  auxiliary_identity_id        = azurerm_user_assigned_identity.auxiliary_function.id
  auxiliary_identity_client_id = azurerm_user_assigned_identity.auxiliary_function.client_id

  # ACR — same registry as the API container apps
  acr_login_server   = module.container_registry.login_server
  acr_admin_username = module.container_registry.admin_username
  acr_admin_password = module.container_registry.admin_password

  # Service Bus — FQNS and topic/subscription for KEDA scaler + runtime
  service_bus_fqns              = module.service_bus.namespace_fqns
  service_bus_topic_name        = module.service_bus.topic_name
  service_bus_subscription_name = module.service_bus.email_processor_subscription_name

  # Key Vault — for KV-backed secret references
  key_vault_uri = module.key_vault.vault_uri

  # Scale to zero in sandbox — no cost when idle; KEDA activates on messages
  min_replicas       = 0
  max_replicas       = 2
  keda_message_count = 5

  # Non-secret env vars — must be Terraform-managed so they survive every
  # terraform apply (unlike vars set only via az containerapp update --set-env-vars).
  environment_variables = [
    { name = "API_BASE_URL",                    value = var.api_base_url },
    { name = "EMAIL_ACTION_TOKEN_EXPIRY_HOURS", value = tostring(var.email_action_token_expiry_hours) },
  ]

  # Secrets from Key Vault — fetched at runtime via managed identity
  kv_secret_references = [
    { env_name = "SQL_SERVER",                    kv_secret_name = "SQL-SERVER" },
    { env_name = "SQL_DATABASE",                  kv_secret_name = "SQL-DATABASE" },
    { env_name = "SQL_USER",                      kv_secret_name = "SQL-USER" },
    { env_name = "SQL_PASSWORD",                  kv_secret_name = "SQL-PASSWORD" },
    { env_name = "GMAIL_APP_PASSWORD",            kv_secret_name = "GMAIL-APP-PASSWORD" },
    { env_name = "FROM_EMAIL",                    kv_secret_name = "FROM-EMAIL" },
    { env_name = "FROM_NAME",                     kv_secret_name = "FROM-NAME" },
    { env_name = "EMAIL_ACTION_SECRET_KEY",       kv_secret_name = "EMAIL-ACTION-SECRET-KEY" },
    { env_name = "APPLICATIONINSIGHTS_CONNECTION_STRING", kv_secret_name = "APPINSIGHTS-CONNECTION-STRING-BACKEND" },
  ]

  # KV access policy and Service Bus RBAC must exist before the Container App starts
  depends_on = [
    azurerm_key_vault_access_policy.auxiliary_function,
    module.service_bus,
    module.container_apps,
  ]

  tags = local.tags
}

# ── 10b. Integrity Check Container App ───────────────────────────────────────
# award-integrity-check-sandbox — async fraud detection worker.
# Consumes nomination.submitted from the fraud-processor subscription,
# runs fraud_check.py (RF + semantic features), writes scores to SQL,
# and re-publishes nomination.created or nomination.fraud-flagged.
#
# Uses the same auxiliary-container-app module as award-auxiliary-sandbox —
# same KEDA / Service Bus / KV pattern, different subscription + resources.
# Higher CPU/memory than the auxiliary worker: sentence-transformers + sklearn
# need ~500 MB RAM and meaningful CPU for inference.
module "integrity_check" {
  source = "../../modules/auxiliary-container-app"

  resource_group_name          = var.resource_group_name
  location                     = var.location_primary
  app_name                     = var.integrity_check_container_app_name
  environment                  = var.environment
  container_app_environment_id = module.container_apps.cae_primary_id

  auxiliary_identity_id        = azurerm_user_assigned_identity.integrity_check.id
  auxiliary_identity_client_id = azurerm_user_assigned_identity.integrity_check.client_id

  acr_login_server   = module.container_registry.login_server
  acr_admin_username = module.container_registry.admin_username
  acr_admin_password = module.container_registry.admin_password

  service_bus_fqns              = module.service_bus.namespace_fqns
  service_bus_topic_name        = module.service_bus.topic_name
  service_bus_subscription_name = module.service_bus.fraud_processor_subscription_name

  key_vault_uri = module.key_vault.vault_uri

  # Scale to zero — fraud check is async so cold-start latency is acceptable.
  min_replicas       = 0
  max_replicas       = 2
  keda_message_count = 1   # 1 replica per pending nomination for fast processing

  # ML inference workload: sentence-transformers + PyTorch need ~500 MB RAM.
  # Azure Consumption plan requires cpu:memory ratio of 1:2 — 1.0 vCPU / 2Gi
  # is the smallest valid combination that fits the model comfortably.
  cpu    = 1.0
  memory = "2Gi"

  environment_variables = [
    { name = "AZURE_STORAGE_ACCOUNT", value = module.storage.storage_account_name },
    { name = "MODEL_CONTAINER",       value = module.storage.ml_models_container_name },
  ]

  kv_secret_references = [
    { env_name = "SQL_SERVER",       kv_secret_name = "SQL-SERVER" },
    { env_name = "SQL_DATABASE",     kv_secret_name = "SQL-DATABASE" },
    { env_name = "SQL_USER",         kv_secret_name = "SQL-USER" },
    { env_name = "SQL_PASSWORD",     kv_secret_name = "SQL-PASSWORD" },
    { env_name = "AZURE_STORAGE_KEY", kv_secret_name = "AZURE-STORAGE-KEY" },
    { env_name = "APPLICATIONINSIGHTS_CONNECTION_STRING", kv_secret_name = "APPINSIGHTS-CONNECTION-STRING-BACKEND" },
  ]

  depends_on = [
    azurerm_key_vault_access_policy.integrity_check,
    azurerm_role_assignment.integrity_check_blob_reader,
    azurerm_role_assignment.integrity_check_sb_sender,
    module.service_bus,
    module.container_apps,
  ]

  tags = local.tags
}

# ── 11. Fraud Analytics Job ───────────────────────────────────────────────────
# Scheduled Container Apps Job: weekly RF retrain + graph pattern detection.
# Runs in the primary CAE alongside the auxiliary worker (same environment,
# separate isolation — job has its own MI, image, and resource allocation).
module "fraud_analytics_job" {
  source = "../../modules/fraud-analytics-job"

  resource_group_name          = var.resource_group_name
  location                     = var.location_primary
  job_name                     = var.fraud_analytics_job_name
  environment                  = var.environment
  container_app_environment_id = module.container_apps.cae_primary_id

  # Identity — pre-authorized for KV above; must also be added as a SQL
  # contained user by DBA: CREATE USER [id-award-fraud-analytics-sandbox]
  # FROM EXTERNAL PROVIDER; ALTER ROLE db_datareader ADD MEMBER [...];
  analytics_identity_id        = azurerm_user_assigned_identity.fraud_analytics_job.id
  analytics_identity_client_id = azurerm_user_assigned_identity.fraud_analytics_job.client_id

  # ACR — same registry as all other container apps
  acr_login_server   = module.container_registry.login_server
  acr_admin_username = module.container_registry.admin_username
  acr_admin_password = module.container_registry.admin_password

  # Key Vault — for KV-backed secret references
  key_vault_uri = module.key_vault.vault_uri

  # Storage — for .pkl model upload after training
  storage_account_name = module.storage.storage_account_name
  model_container_name = module.storage.ml_models_container_name

  # Schedule — override default here if needed per environment
  cron_expression = var.fraud_analytics_cron

  # Non-secret env vars
  environment_variables = [
    { name = "GRAPH_FINDINGS_TABLE",      value = "dbo.GraphPatternFindings" },
    { name = "LOGGING_LEVEL",             value = var.logging_level },
    { name = "DETECTION_WINDOW_DAYS",     value = tostring(var.fraud_analytics_detection_window_days) },
    { name = "RING_MAX_CLUSTER_SIZE",     value = tostring(var.fraud_analytics_ring_max_cluster_size) },
    # Post-training cache-refresh callback — job POSTs here after uploading new pkls.
    # Uses the primary app's internal FQDN (ACA-to-ACA routing within the same CAE).
    { name = "API_BASE_URL",              value = "https://${var.app_name_primary}.internal.${module.container_apps.cae_primary_default_domain}" },
  ]

  # Secrets from Key Vault — SQL + Storage + callback secret
  kv_secret_references = [
    { env_name = "SQL_SERVER",          kv_secret_name = "SQL-SERVER" },
    { env_name = "SQL_DATABASE",        kv_secret_name = "SQL-DATABASE" },
    { env_name = "SQL_USER",            kv_secret_name = "SQL-USER" },
    { env_name = "SQL_PASSWORD",        kv_secret_name = "SQL-PASSWORD" },
    { env_name = "AZURE_STORAGE_KEY",   kv_secret_name = "AZURE-STORAGE-KEY" },
    { env_name = "APPLICATIONINSIGHTS_CONNECTION_STRING", kv_secret_name = "APPINSIGHTS-CONNECTION-STRING-BACKEND" },
    # Shared secret for /api/internal/refresh-fraud-model — must match FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET on the API.
    { env_name = "FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET", kv_secret_name = "FRAUD-ANALYTICS-JOB-WEBHOOK-SECRET" },
  ]

  depends_on = [
    azurerm_key_vault_access_policy.fraud_analytics_job,
    module.container_apps,
    module.storage,
  ]

  tags = local.tags
}

# ── 13. Front Door ────────────────────────────────────────────────────────────
module "front_door" {
  source = "../../modules/front-door"

  resource_group_name     = var.resource_group_name
  afd_profile_name        = var.afd_profile_name
  afd_endpoint_name       = var.afd_endpoint_name
  container_app_primary_fqdn   = module.container_apps.primary_app_fqdn
  container_app_secondary_fqdn = module.container_apps.secondary_app_fqdn
  tags                    = local.tags
  depends_on              = [azurerm_resource_group.rg, module.container_apps]
}

# ── DNS — CNAME records for SWA custom domains ────────────────────────────────
# terian-services.com is managed in Azure DNS (rg_platform).
# One CNAME record per entry in swa_custom_domains, each pointing to the
# SWA default hostname. Validation is handled by the SWA custom domain
# resource (cname-delegation) which reads these same records.
#
# Current domains:
#   sandbox-awards.terian-services.com  — main sandbox / existing tenants
#   acme-awards.terian-services.com     — ACME Corp tenant
#   demo-awards.terian-services.com     — public demo (self-registration)
data "azurerm_dns_zone" "terian_services" {
  count               = length(var.swa_custom_domains) > 0 ? 1 : 0
  name                = "terian-services.com"
  resource_group_name = var.dns_zone_resource_group
}

resource "azurerm_dns_cname_record" "swa_custom_domains" {
  for_each            = toset(var.swa_custom_domains)
  name                = split(".", each.value)[0]   # "sandbox-awards", "acme-awards", "demo-awards"
  zone_name           = data.azurerm_dns_zone.terian_services[0].name
  resource_group_name = var.dns_zone_resource_group
  ttl                 = 3600
  record              = module.static_web_app.default_hostname
  tags                = local.tags
  depends_on          = [module.static_web_app]
}

# ── 10. Static Web App ────────────────────────────────────────────────────────
module "static_web_app" {
  source = "../../modules/static-web-app"

  resource_group_name = var.resource_group_name
  location            = var.location_primary
  app_name            = var.swa_name
  afd_hostname        = module.front_door.afd_endpoint_hostname
  vite_api_url        = "https://${module.front_door.afd_endpoint_hostname}"
  vite_api_client_id                 = module.app_registrations.api_client_id
  vite_client_id                     = module.app_registrations.frontend_client_id
  vite_api_scope                     = module.app_registrations.api_scope
  vite_appinsights_connection_string = module.application_insights.frontend_connection_string
  demo_allowed_emails                = var.demo_allowed_emails
  tags                               = local.tags
  depends_on                         = [azurerm_resource_group.rg]
}

# Custom domain — lives here (not in the module) so it can explicitly wait for
# the DNS CNAME record before Azure attempts cname-delegation validation.
resource "azurerm_static_web_app_custom_domain" "swa_custom_domains" {
  for_each          = toset(var.swa_custom_domains)
  static_web_app_id = module.static_web_app.static_web_app_id
  domain_name       = each.value
  validation_type   = "cname-delegation"
  depends_on        = [azurerm_dns_cname_record.swa_custom_domains]

  lifecycle {
    # validation_type is not returned by the API after the domain is validated,
    # so it is absent from imported state and would force replacement on every plan.
    ignore_changes = [validation_type]
  }
}

# ── 12. HRBP SLA Logic App ────────────────────────────────────────────────────
# Consumption (multi-tenant) Logic App — no dedicated hosting plan required.
#
# Purpose: daily weekday morning check for nominations that have exceeded their
# HRBP review SLA. Calls POST /api/internal/checkPendingHRBPReview on the
# primary backend, which queries dbo.Nominations for rows in PendingHRBPReview
# older than HRBP_SLA_HOURS and publishes nomination.hrbp-sla-breach events.
#
# Why Logic Apps instead of an asyncio background loop inside the backend?
# The backend Container App scales to zero (min_replicas = 0 in non-prod tiers).
# An asyncio loop stops when the container is not running. The Logic App is an
# external scheduler — it fires regardless of backend replica count, and its
# HTTP action wakes the backend if needed.
#
# Security: X-Internal-Key header with HRBP-SLA-WEBHOOK-SECRET (same pattern
# as the fraud-analytics-job callback). The secret is stored in Key Vault and
# referenced as a sensitive variable — it does NOT appear in plan output.
resource "azurerm_logic_app_workflow" "hrbp_sla" {
  name                = "la-award-hrbp-sla-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location_primary
  tags                = local.tags

  depends_on = [azurerm_resource_group.rg]
}

# Recurrence trigger — fires every weekday at 08:00 UTC.
# frequency = "Week" + on_these_days gives true weekday-only semantics
# (as opposed to frequency = "Day" which includes weekends).
resource "azurerm_logic_app_trigger_recurrence" "hrbp_sla_trigger" {
  name         = "WeekdayMorning"
  logic_app_id = azurerm_logic_app_workflow.hrbp_sla.id
  frequency    = "Week"
  interval     = 1

  schedule {
    on_these_days    = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    at_these_hours   = [8]
    at_these_minutes = [0]
  }
}

# HTTP action — POST to the backend's internal SLA check endpoint.
# Uses the Front Door URL so the Logic App (which runs outside our VNet)
# can reach the backend over the public internet with WAF protection.
#
# Note: hrbp_sla_webhook_secret is marked sensitive in variables.tf so
# Terraform will not print it in plan/apply output. It is stored in state
# (same trade-off as all other secrets in this environment). For a future
# hardening pass, replace the header value with an Azure Key Vault reference
# fetched via a preceding azurerm_logic_app_action_http Key Vault action.
resource "azurerm_logic_app_action_http" "hrbp_sla_check" {
  name         = "CheckPendingHRBPReview"
  logic_app_id = azurerm_logic_app_workflow.hrbp_sla.id
  method       = "POST"
  uri          = "${var.api_base_url}/api/internal/checkPendingHRBPReview"

  headers = {
    "Content-Type"   = "application/json"
    "X-Internal-Key" = var.hrbp_sla_webhook_secret
  }

  # Empty body — the backend derives everything it needs from the DB.
  body = "{}"

  depends_on = [azurerm_logic_app_trigger_recurrence.hrbp_sla_trigger]
}
