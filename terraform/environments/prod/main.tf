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
  location = var.location_east
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
  vite_tenant_id     = data.azuread_client_config.current.tenant_id
  vite_api_client_id = data.azuread_application.api.client_id
  vite_client_id     = data.azuread_application.frontend.client_id
  vite_api_scope     = "api://${data.azuread_application.api.client_id}/access_as_user"
}

# ── 1. Networking ─────────────────────────────────────────────────────────────
module "networking" {
  source = "../../modules/networking"

  resource_group_name     = var.resource_group_name
  environment             = var.environment
  vnet_east_address_space = "10.0.0.0/16"
  vnet_west_address_space = "10.1.0.0/16"
  tags                    = local.tags
  depends_on              = [azurerm_resource_group.rg]
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
  aca_subnet_ids             = [module.networking.subnet_aca_east_id, module.networking.subnet_aca_west_id]
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_blob_id
  tags                       = local.tags
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
  aca_subnet_ids             = [module.networking.subnet_aca_east_id, module.networking.subnet_aca_west_id]
  private_endpoint_subnet_id = module.networking.subnet_private_endpoints_id
  private_dns_zone_id        = module.networking.dns_zone_kv_id
  aca_principal_ids          = []
  tags                       = local.tags

  # var.secrets (from terraform.tfvars) supplies: SQL-USER, SQL-PASSWORD, GMAIL-APP-PASSWORD
  # Remaining secrets are derived from other module outputs so they stay in sync automatically.
  secrets = merge(var.secrets, {
    AZURE-STORAGE-KEY     = module.storage.primary_access_key
    AZURE-OPENAI-KEY      = module.openai.primary_access_key
    AZURE-OPENAI-ENDPOINT = module.openai.endpoint
    SQL-SERVER            = module.sql.server_fqdn
    SQL-DATABASE          = module.sql.database_name
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

  resource_group_name = var.resource_group_name
  workspace_name_east = var.workspace_name_east
  workspace_name_west = var.workspace_name_west
  tags                = local.tags
}

# ── 8. Container Apps ─────────────────────────────────────────────────────────
module "container_apps" {
  source = "../../modules/container-apps"

  resource_group_name             = var.resource_group_name
  cae_name_east                   = var.cae_name_east
  cae_name_west                   = var.cae_name_west
  app_name_east                   = var.app_name_east
  app_name_west                   = var.app_name_west
  subnet_aca_east_id              = module.networking.subnet_aca_east_id
  subnet_aca_west_id              = module.networking.subnet_aca_west_id
  min_replicas                    = var.min_replicas
  max_replicas                    = var.max_replicas
  log_analytics_workspace_east_id = module.log_analytics.workspace_east_id
  log_analytics_workspace_west_id = module.log_analytics.workspace_west_id
  acr_login_server                = module.container_registry.login_server
  acr_admin_username              = module.container_registry.admin_username
  acr_admin_password              = module.container_registry.admin_password
  key_vault_uri                   = module.key_vault.vault_uri

  # Non-secret config — passed as plain env vars
  environment_variables = [
    { name = "AZURE_STORAGE_ACCOUNT",           value = module.storage.storage_account_name },
    { name = "MODEL_CONTAINER",                 value = module.storage.ml_models_container_name },
    { name = "EXTRACTS_CONTAINER",              value = module.storage.extracts_container_name },
    { name = "AZURE_OPENAI_MODEL",              value = module.openai.model_deployment_name },
    { name = "KEY_VAULT_URL",                   value = module.key_vault.vault_uri },
    { name = "ENVIRONMENT",                     value = var.environment },
    { name = "REGION",                          value = var.location_east },
    { name = "CONTAINER_APP_NAME",              value = var.app_name_east },
    { name = "AZURE_OPENAI_API_VERSION",        value = var.openai_api_version },
    { name = "MODEL_BLOB_NAME",                 value = var.model_blob_name },
    { name = "API_BASE_URL",                    value = var.api_base_url },
    { name = "LOGGING_LEVEL",                   value = var.logging_level },
    { name = "BLOB_SAS_EXPIRY_HOURS",           value = tostring(var.blob_sas_expiry_hours) },
    { name = "EMAIL_ACTION_TOKEN_EXPIRY_HOURS", value = tostring(var.email_action_token_expiry_hours) },
  ]

  # Secret config — fetched from Key Vault at runtime via managed identity
  kv_secret_references = [
    { env_name = "SQL_SERVER",            kv_secret_name = "SQL-SERVER" },
    { env_name = "SQL_DATABASE",          kv_secret_name = "SQL-DATABASE" },
    { env_name = "SQL_USER",              kv_secret_name = "SQL-USER" },
    { env_name = "SQL_PASSWORD",          kv_secret_name = "SQL-PASSWORD" },
    { env_name = "AZURE_STORAGE_KEY",     kv_secret_name = "AZURE-STORAGE-KEY" },
    { env_name = "GMAIL_APP_PASSWORD",    kv_secret_name = "GMAIL-APP-PASSWORD" },
    { env_name = "AZURE_OPENAI_KEY",      kv_secret_name = "AZURE-OPENAI-KEY" },
    { env_name = "AZURE_OPENAI_ENDPOINT", kv_secret_name = "AZURE-OPENAI-ENDPOINT" },
  ]

  tags = local.tags
}

# ── Key Vault access policies for Container Apps ──────────────────────────────
# Standalone resources break the circular dependency:
#   KV is created first (no ACA deps) → Container Apps created with KEY_VAULT_URL
#   → these policies created last, granting managed identities secret read access.
resource "azurerm_key_vault_access_policy" "aca_east" {
  key_vault_id = module.key_vault.key_vault_id
  tenant_id    = data.azuread_client_config.current.tenant_id
  object_id    = module.container_apps.east_principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [module.key_vault, module.container_apps]
}

resource "azurerm_key_vault_access_policy" "aca_west" {
  key_vault_id = module.key_vault.key_vault_id
  tenant_id    = data.azuread_client_config.current.tenant_id
  object_id    = module.container_apps.west_principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [module.key_vault, module.container_apps]
}

# ── 9. Front Door ─────────────────────────────────────────────────────────────
module "front_door" {
  source = "../../modules/front-door"

  resource_group_name     = var.resource_group_name
  afd_profile_name        = var.afd_profile_name
  afd_endpoint_name       = var.afd_endpoint_name
  container_app_east_fqdn = module.container_apps.east_app_fqdn
  container_app_west_fqdn = module.container_apps.west_app_fqdn
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
  vite_tenant_id     = local.vite_tenant_id
  vite_api_client_id = local.vite_api_client_id
  vite_client_id     = local.vite_client_id
  vite_api_scope     = local.vite_api_scope

  tags = local.tags
}

# ── 11. Grafana ───────────────────────────────────────────────────────────────
module "grafana" {
  source = "../../modules/grafana"

  resource_group_name             = var.resource_group_name
  grafana_name                    = var.grafana_name
  log_analytics_workspace_east_id = module.log_analytics.workspace_east_id
  log_analytics_workspace_west_id = module.log_analytics.workspace_west_id
  tags                            = local.tags
}
