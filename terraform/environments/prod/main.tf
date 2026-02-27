# environments/prod/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Production environment — wires all modules together
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
  location = "eastus"
  tags     = local.tags
}

# ── 1. Networking ─────────────────────────────────────────────────────────────
module "networking" {
  source = "../../modules/networking"

  resource_group_name     = var.resource_group_name
  environment             = var.environment
  vnet_east_address_space = "10.0.0.0/16"
  vnet_west_address_space = "10.1.0.0/16"
  tags                    = local.tags
}

# ── 2. SQL ────────────────────────────────────────────────────────────────────
module "sql" {
  source = "../../modules/sql"

  resource_group_name        = var.resource_group_name
  server_name                = var.sql_server_name
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

  environment_variables = [
    { name = "AZURE_OPENAI_ENDPOINT",    value = module.openai.endpoint },
    { name = "AZURE_OPENAI_MODEL",       value = module.openai.model_deployment_name },
    { name = "KEY_VAULT_URL",            value = module.key_vault.vault_uri },
    { name = "DB_SERVER",                value = module.sql.server_fqdn },
    { name = "DB_NAME",                  value = module.sql.database_name },
    { name = "AZURE_STORAGE_ACCOUNT",    value = module.storage.storage_account_name },
    { name = "BLOB_CONTAINER_EXTRACTS",  value = module.storage.extracts_container_name },
    { name = "BLOB_CONTAINER_ML_MODELS", value = module.storage.ml_models_container_name },
    { name = "ENVIRONMENT",              value = var.environment },
  ]

  tags = local.tags
}

# ── 9. Front Door ─────────────────────────────────────────────────────────────
module "front_door" {
  source = "../../modules/front-door"

  resource_group_name     = var.resource_group_name
  afd_profile_name        = var.afd_profile_name
  afd_endpoint_name       = var.afd_endpoint_name
  cae_east_id             = module.container_apps.cae_east_id
  cae_west_id             = module.container_apps.cae_west_id
  cae_east_static_ip      = module.container_apps.cae_east_static_ip
  cae_west_static_ip      = module.container_apps.cae_west_static_ip
  cae_east_default_domain = module.container_apps.cae_east_default_domain
  cae_west_default_domain = module.container_apps.cae_west_default_domain
  tags                    = local.tags
}

# ── 10. Static Web App ────────────────────────────────────────────────────────
module "static_web_app" {
  source = "../../modules/static-web-app"

  resource_group_name = var.resource_group_name
  app_name            = var.swa_name
  afd_hostname        = module.front_door.afd_endpoint_hostname
  tags                = local.tags
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
