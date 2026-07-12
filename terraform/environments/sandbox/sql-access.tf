# environments/sandbox/sql-access.tf
# -----------------------------------------------------------------------------
# ADR-0001 -- Entra groups for SQL access. The migration job's MI joins
# sql-migrations-<env> (see schema-migration-job.tf); the SQL server's Entra
# admin is wired into module.sql (main.tf) via the admins_group_* outputs.
# -----------------------------------------------------------------------------
module "sql_access" {
  source = "../../modules/sql-access"

  environment = var.environment

  # Runtime workloads that get db_datareader + db_datawriter via group membership.
  runtime_identity_principal_ids = {
    "aca-primary"     = azurerm_user_assigned_identity.aca_primary.principal_id
    "aca-secondary"   = azurerm_user_assigned_identity.aca_secondary.principal_id
    "auxiliary"       = azurerm_user_assigned_identity.auxiliary_function.principal_id
    "fraud-analytics" = azurerm_user_assigned_identity.fraud_analytics_job.principal_id
    "payroll-broker"  = azurerm_user_assigned_identity.payroll_broker.principal_id
    "integrity-check" = azurerm_user_assigned_identity.integrity_check.principal_id
  }
}
