# modules/sql-access/main.tf
# -----------------------------------------------------------------------------
# SQL access governance (ADR-0001)
#
# Entra groups that replace the shared personal SQL login. The contained DB
# users map to these groups:
#   - sql-app-readwrite-<env>  : runtime Managed Identities  -> db_datareader + db_datawriter
#   - sql-migrations-<env>     : the schema-migration ACA Job's MI -> db_ddladmin
#   - sql-admins-<env>         : Entra admin on the SQL server (set in the sql module)
#
# Migrations run as an in-VNet ACA Job (its MI joins sql-migrations-<env>, wired
# in environments/<env>/schema-migration-job.tf) -- GitHub-hosted runners can't
# reach the private-endpoint SQL, so no GitHub-OIDC-to-SQL identity exists here.
#
# In-database users + grants are applied MANUALLY from a firewall-whitelisted
# machine (pre-lockdown) via scripts/soc2-sql-managed-identity/db-access-grants.sql.
# -----------------------------------------------------------------------------

data "azuread_client_config" "current" {}

locals {
  rw_group_name  = var.readwrite_group_name != "" ? var.readwrite_group_name : "sql-app-readwrite-${var.environment}"
  mig_group_name = var.migrations_group_name != "" ? var.migrations_group_name : "sql-migrations-${var.environment}"
  adm_group_name = var.admins_group_name != "" ? var.admins_group_name : "sql-admins-${var.environment}"
}

resource "azuread_group" "sql_app_readwrite" {
  display_name     = local.rw_group_name
  description      = "Runtime SQL access (db_datareader + db_datawriter) -- ${var.environment}. ADR-0001."
  security_enabled = true
  owners           = [data.azuread_client_config.current.object_id]

  lifecycle {
    ignore_changes = [members]
  }
}

resource "azuread_group" "sql_migrations" {
  display_name     = local.mig_group_name
  description      = "Schema-migration SQL access (db_ddladmin) -- ${var.environment}. ADR-0001."
  security_enabled = true
  owners           = [data.azuread_client_config.current.object_id]

  lifecycle {
    ignore_changes = [members]
  }
}

resource "azuread_group" "sql_admins" {
  display_name     = local.adm_group_name
  description      = "Entra admin on the SQL server -- ${var.environment}. ADR-0001 (break-glass + bootstrap)."
  security_enabled = true
  owners           = [data.azuread_client_config.current.object_id]

  lifecycle {
    ignore_changes = [members]
  }
}

# Runtime Managed Identities -> read/write group.
resource "azuread_group_member" "runtime_readwrite" {
  for_each = var.runtime_identity_principal_ids

  group_object_id  = azuread_group.sql_app_readwrite.object_id
  member_object_id = each.value
}
