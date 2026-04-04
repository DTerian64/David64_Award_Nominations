# environments/prod/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Production environment — wires all modules together
# App registrations are READ from existing Azure AD (not managed by Terraform)
# ─────────────────────────────────────────────────────────────────────────────

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
}

# ── Azure AD — read existing prod app registrations (do not modify) ───────────
data "azuread_client_config" "current" {}

data "azuread_application" "api" {
  display_name = "Award Nomination"
}

data "azuread_application" "frontend" {
  display_name = "Award Nomination System - Frontend"
}

locals {
  # vite_tenant_id removed — frontend now uses /organizations authority,
  # no tenant ID needed at build time.
  vite_api_client_id = data.azuread_application.api.client_id
  vite_client_id     = data.azuread_application.frontend.client_id
  vite_api_scope     = "api://${data.azuread_application.api.client_id}/access_as_user"
}

# ── 1. Networking ─────────────────────────────────────────────────────────────
module "networking" {
  source = "../../modules/networking"

  resource_group_name          = var.resource_group_name
  environment                  = var.environment
  location_primary             = var.location_primary
  location_secondary           = var.location_secondary
  vnet_primary_address_space   = "10.0.0.0/16"
  vnet_secondary_address_space = "10.1.0.0/16"
  tags                         = local.tags
  depends_on                   = [azurerm_resource_group.rg]
}

# ── 2. SQL ────────────────────────────────────────────────────────────────────
module "sql" {
  source = "../../modules/sql"

  resource_group_name        = var.resource_group_name
  server_name                = var.sql_server_name
  database_name              = var.sql_database_name
  admin_login                = var.sql_admin_login
  admin_password             = var.sql_admin_password
  allowed_ips                = var.my_ips
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_sql_id
  tags                       = local.tags
}

# ── 3. Container Registry ─────────────────────────────────────────────────────
module "container_registry" {
  source = "../../modules/container-registry"

  resource_group_name        = var.resource_group_name
  acr_name                   = var.acr_name
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_acr_id
  tags                       = local.tags
}

# ── 4. Storage ────────────────────────────────────────────────────────────────
module "storage" {
  source = "../../modules/storage"

  resource_group_name        = var.resource_group_name
  storage_account_name       = var.storage_account_name
  allowed_ips                = var.my_ips
  aca_subnet_ids             = [module.networking.subnet_aca_primary_id, module.networking.subnet_aca_secondary_id]
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_blob_id
  tags                       = local.tags
}

# ── 4b. User-Assigned Managed Identities ─────────────────────────────────────
# Created BEFORE Key Vault access policies and Container Apps.
# This eliminates the system-assigned identity race condition where Azure tries to
# validate KV-backed secrets before the access policy for the new identity exists.
# Dependency order: MI → KV access policy → resource (with KV secrets)
resource "azurerm_user_assigned_identity" "aca_primary" {
  name                = "id-award-api-eastus-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location_primary
  tags                = local.tags
  depends_on          = [azurerm_resource_group.rg]
}

resource "azurerm_user_assigned_identity" "aca_secondary" {
  name                = "id-award-api-westus-${var.environment}"
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

# ── 5. Key Vault ──────────────────────────────────────────────────────────────
# aca_principal_ids is intentionally empty here to avoid a circular dependency:
#   Key Vault needs Container App principal IDs → Container Apps need KEY_VAULT_URL.
# Access policies are added as standalone resources below (after Container Apps).
module "key_vault" {
  source = "../../modules/key-vault"

  resource_group_name        = var.resource_group_name
  key_vault_name             = var.key_vault_name
  allowed_ips                = var.my_ips
  aca_subnet_ids             = [module.networking.subnet_aca_primary_id, module.networking.subnet_aca_secondary_id]
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_kv_id
  aca_principal_ids          = []
  tags                       = local.tags

  # var.secrets (from terraform.tfvars) supplies: SQL-USER, SQL-PASSWORD, GMAIL-APP-PASSWORD
  # Remaining secrets are derived from other module outputs so they stay in sync automatically.
  secrets = merge(var.secrets, {
    AZURE-STORAGE-KEY                     = module.storage.primary_access_key
    AZURE-OPENAI-KEY                      = module.openai.primary_access_key
    AZURE-OPENAI-ENDPOINT                 = module.openai.endpoint
    SQL-SERVER                            = module.sql.server_fqdn
    SQL-DATABASE                          = module.sql.database_name
    APPINSIGHTS-CONNECTION-STRING-BACKEND = module.application_insights.backend_connection_string
  })
}

# ── 6. OpenAI ─────────────────────────────────────────────────────────────────
module "openai" {
  source = "../../modules/openai"

  resource_group_name        = var.resource_group_name
  openai_name                = var.openai_name
  model_capacity_tpm         = var.model_capacity_tpm
  allowed_ips                = var.my_ips
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_openai_id
  tags                       = local.tags
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
  min_replicas                    = var.min_replicas
  max_replicas                    = var.max_replicas
  log_analytics_workspace_primary_id   = module.log_analytics.workspace_primary_id
  log_analytics_workspace_secondary_id = module.log_analytics.workspace_secondary_id
  acr_login_server                = module.container_registry.login_server
  acr_admin_username              = module.container_registry.admin_username
  acr_admin_password              = module.container_registry.admin_password
  key_vault_uri                   = module.key_vault.vault_uri
  aca_primary_identity_id              = azurerm_user_assigned_identity.aca_primary.id
  aca_primary_identity_client_id       = azurerm_user_assigned_identity.aca_primary.client_id
  aca_secondary_identity_id            = azurerm_user_assigned_identity.aca_secondary.id
  aca_secondary_identity_client_id     = azurerm_user_assigned_identity.aca_secondary.client_id
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
    { name = "LOGGING_LEVEL",                   value = var.logging_level },
    { name = "BLOB_SAS_EXPIRY_HOURS",           value = tostring(var.blob_sas_expiry_hours) },
    { name = "EMAIL_ACTION_TOKEN_EXPIRY_HOURS", value = tostring(var.email_action_token_expiry_hours) },
    # CLIENT_ID is required by auth.py for JWT audience validation (api://<client_id>).
    { name = "CLIENT_ID",                       value = local.vite_api_client_id },
    # Service Bus — neither FQNS nor topic name is sensitive; MI credential grants access.
    { name = "SERVICE_BUS_FQNS",                value = module.service_bus.namespace_fqns },
    { name = "SERVICE_BUS_TOPIC_NAME",          value = module.service_bus.topic_name },
  ]

  # Secret config — fetched from Key Vault at runtime via managed identity
  kv_secret_references = [
    { env_name = "SQL_SERVER",            kv_secret_name = "SQL-SERVER" },
    { env_name = "SQL_DATABASE",          kv_secret_name = "SQL-DATABASE" },
    { env_name = "SQL_USER",              kv_secret_name = "SQL-USER" },
    { env_name = "SQL_PASSWORD",          kv_secret_name = "SQL-PASSWORD" },
    { env_name = "AZURE_STORAGE_KEY",     kv_secret_name = "AZURE-STORAGE-KEY" },
    { env_name = "GMAIL_APP_PASSWORD",    kv_secret_name = "GMAIL-APP-PASSWORD" },
    { env_name = "AZURE_OPENAI_KEY",                     kv_secret_name = "AZURE-OPENAI-KEY" },
    { env_name = "AZURE_OPENAI_ENDPOINT",                kv_secret_name = "AZURE-OPENAI-ENDPOINT" },
    { env_name = "APPLICATIONINSIGHTS_CONNECTION_STRING", kv_secret_name = "APPINSIGHTS-CONNECTION-STRING-BACKEND" },
  ]

  tags = local.tags
}

# ── Key Vault access policies for Container Apps ──────────────────────────────
# Reference the user-assigned MIs (created above) — not the Container Apps.
# This breaks the ordering race: MI exists → KV policy granted → Container App
# created with identity already authorized. No more 5s timeout errors.
resource "azurerm_key_vault_access_policy" "aca_primary" {
  key_vault_id = module.key_vault.key_vault_id
  tenant_id    = data.azuread_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.aca_primary.principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [module.key_vault, azurerm_user_assigned_identity.aca_primary]
}

resource "azurerm_key_vault_access_policy" "aca_secondary" {
  key_vault_id = module.key_vault.key_vault_id
  tenant_id    = data.azuread_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.aca_secondary.principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [module.key_vault, azurerm_user_assigned_identity.aca_secondary]
}

# KV access policy — Auxiliary Function
resource "azurerm_key_vault_access_policy" "auxiliary_function" {
  key_vault_id = module.key_vault.key_vault_id
  tenant_id    = data.azuread_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.auxiliary_function.principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [module.key_vault, azurerm_user_assigned_identity.auxiliary_function]
}

# ── 9. Service Bus ────────────────────────────────────────────────────────────
# Production: Standard SKU for now — upgrade to Premium if private endpoint
# network isolation is required (set sku = "Premium" and supply
# private_endpoint_subnet_id + private_dns_zone_id; also update the networking
# module to create the privatelink.servicebus.windows.net DNS zone).
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
    "auxiliary-function" = azurerm_user_assigned_identity.auxiliary_function.principal_id
  }

  depends_on = [azurerm_resource_group.rg]
}

# ── 10. Auxiliary Container App ───────────────────────────────────────────────
# Event-driven worker: Service Bus → KEDA → container (no HTTP ingress).
# min_replicas = 1 in prod — keeps one instance warm to avoid cold-start latency.
# max_replicas = 5 — burst capacity for high nomination/approval volumes.
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

  # Always-on in prod — one warm replica; KEDA scales up to 5 under load
  min_replicas       = 1
  max_replicas       = 5
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

# ── 11. Front Door ────────────────────────────────────────────────────────────
module "front_door" {
  source = "../../modules/front-door"

  resource_group_name     = var.resource_group_name
  afd_profile_name        = var.afd_profile_name
  afd_endpoint_name       = var.afd_endpoint_name
  container_app_primary_fqdn   = module.container_apps.primary_app_fqdn
  container_app_secondary_fqdn = module.container_apps.secondary_app_fqdn
  tags                    = local.tags
  depends_on              = [module.container_apps]
}

# ── 10. Static Web App ────────────────────────────────────────────────────────
module "static_web_app" {
  source = "../../modules/static-web-app"

  resource_group_name = var.resource_group_name
  app_name            = var.swa_name
  afd_hostname        = module.front_door.afd_endpoint_hostname

  # Azure AD values wired from existing app registrations
  vite_api_url       = "https://${module.front_door.afd_endpoint_hostname}"
  vite_api_client_id                 = local.vite_api_client_id
  vite_client_id                     = local.vite_client_id
  vite_api_scope                     = local.vite_api_scope
  vite_appinsights_connection_string = module.application_insights.frontend_connection_string

  tags = local.tags
}

# ── 11. Grafana ───────────────────────────────────────────────────────────────
module "grafana" {
  source = "../../modules/grafana"

  resource_group_name             = var.resource_group_name
  grafana_name                    = var.grafana_name
  log_analytics_workspace_primary_id   = module.log_analytics.workspace_primary_id
  log_analytics_workspace_secondary_id = module.log_analytics.workspace_secondary_id
  tags                            = local.tags
}
