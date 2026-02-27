# modules/openai/outputs.tf

output "openai_id" {
  description = "Azure OpenAI account resource ID"
  value       = azurerm_cognitive_account.openai.id
}

output "openai_name" {
  description = "Azure OpenAI account name"
  value       = azurerm_cognitive_account.openai.name
}

output "endpoint" {
  description = "Azure OpenAI endpoint URL — inject as AZURE_OPENAI_ENDPOINT env var"
  value       = azurerm_cognitive_account.openai.endpoint
}

output "primary_access_key" {
  description = "Primary API key — inject as AZURE_OPENAI_KEY env var"
  value       = azurerm_cognitive_account.openai.primary_access_key
  sensitive   = true
}

output "model_deployment_name" {
  description = "Model deployment name — inject as AZURE_OPENAI_MODEL env var"
  value       = azurerm_cognitive_deployment.gpt4.name
}

output "private_endpoint_ip" {
  description = "Private IP address of the OpenAI private endpoint"
  value       = azurerm_private_endpoint.openai.private_service_connection[0].private_ip_address
}

# ─────────────────────────────────────────────────────────────────────────────
# QUOTA WARNING
# ─────────────────────────────────────────────────────────────────────────────
# When deploying to a NEW subscription, gpt-4.1 GlobalStandard quota
# is NOT automatically available. You must request it first:
#
#   Portal → Azure OpenAI → Quotas → East US → gpt-4.1 → Request increase
#   Or: az cognitiveservices usage list --location eastus
#
# Terraform apply will fail with "InsufficientQuota" if quota is not approved.
# Quota approval typically takes minutes to hours for GlobalStandard.
# ─────────────────────────────────────────────────────────────────────────────
