# environments/sandbox/schema-migration-job.tf
# -----------------------------------------------------------------------------
# ADR-0001 -- migrations run as an in-VNet ACA Job (private-endpoint SQL is
# unreachable from GitHub-hosted runners). The job authenticates to SQL with a
# user-assigned Managed Identity that is a member of sql-migrations-<env>
# (db_ddladmin). GitHub Actions only triggers it (az containerapp job start).
# -----------------------------------------------------------------------------

# Managed Identity for the migration job (maps to the sql-migrations-<env> DB user).
resource "azurerm_user_assigned_identity" "schema_migration" {
  name                = "id-award-schema-migration-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location_primary
  tags                = local.tags
  depends_on          = [azurerm_resource_group.rg]
}

# Join it to sql-migrations-<env> -> db_ddladmin (the contained DB user maps to the group).
resource "azuread_group_member" "schema_migration_job" {
  group_object_id  = module.sql_access.migrations_group_object_id
  member_object_id = azurerm_user_assigned_identity.schema_migration.principal_id
}

module "schema_migration_job" {
  source = "../../modules/schema-migration-job"

  job_name                     = "award-schema-migration-${var.environment}"
  resource_group_name          = var.resource_group_name
  location                     = var.location_primary
  environment                  = var.environment
  container_app_environment_id = module.container_apps.cae_primary_id

  identity_id        = azurerm_user_assigned_identity.schema_migration.id
  identity_client_id = azurerm_user_assigned_identity.schema_migration.client_id

  acr_login_server   = module.container_registry.login_server
  acr_admin_username = module.container_registry.admin_username
  acr_admin_password = module.container_registry.admin_password

  sql_server_fqdn   = "${var.sql_server_name}.database.windows.net"
  sql_database_name = var.sql_database_name

  tags       = local.tags
  depends_on = [module.container_apps, module.sql_access]
}

output "schema_migration_job_name" {
  description = "ACA Job name -- CI runs: az containerapp job start --name <this> -g <rg>."
  value       = module.schema_migration_job.job_name
}
