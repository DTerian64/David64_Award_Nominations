# modules/fraud-analytics-job/outputs.tf

output "job_name" {
  description = "Name of the Container Apps Job (used by CI/CD to target the correct job)."
  value       = azurerm_container_app_job.fraud_analytics.name
}

output "job_id" {
  description = "Resource ID of the Container Apps Job."
  value       = azurerm_container_app_job.fraud_analytics.id
}
