# modules/payout-orchestrator/outputs.tf

output "function_app_name" {
  description = "Container App name (hosts the Azure Functions runtime)"
  value       = azurerm_container_app.payout_orchestrator.name
}

output "function_app_id" {
  description = "Container App resource ID"
  value       = azurerm_container_app.payout_orchestrator.id
}

output "default_hostname" {
  description = "External FQDN of the payout-orchestrator — use as the webhook base URL for Workday_Proxy callbacks (e.g. https://<hostname>/api/PayoutWebhookTrigger)"
  value       = azurerm_container_app.payout_orchestrator.ingress[0].fqdn
}

output "storage_account_name" {
  description = "Storage account used for Durable Functions orchestration state"
  value       = azurerm_storage_account.fn_storage.name
}
