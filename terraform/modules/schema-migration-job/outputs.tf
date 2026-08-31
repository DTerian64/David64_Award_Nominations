# modules/schema-migration-job/outputs.tf
output "job_name" {
  description = "Container App Job name -- used by CI: az containerapp job start."
  value       = azurerm_container_app_job.schema_migration.name
}
output "job_id" {
  value = azurerm_container_app_job.schema_migration.id
}
