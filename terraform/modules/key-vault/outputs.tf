# modules/key-vault/outputs.tf

output "key_vault_id" {
  description = "Key Vault resource ID"
  value       = azurerm_key_vault.kv.id
}

output "key_vault_name" {
  description = "Key Vault name"
  value       = azurerm_key_vault.kv.name
}

output "vault_uri" {
  description = "Key Vault URI — inject as KEY_VAULT_URL env var in Container Apps"
  value       = azurerm_key_vault.kv.vault_uri
}

output "private_endpoint_ip" {
  description = "Private IP address of the Key Vault private endpoint"
  value       = azurerm_private_endpoint.kv.private_service_connection[0].private_ip_address
}

# ─────────────────────────────────────────────────────────────────────────────
# POST-DEPLOY NOTE
# ─────────────────────────────────────────────────────────────────────────────
# Secrets are NOT managed by Terraform — add them manually after deploy:
#
#   az keyvault secret set --vault-name <name> --name "DB-PASSWORD"      --value "..."
#   az keyvault secret set --vault-name <name> --name "STORAGE-KEY"      --value "..."
#   az keyvault secret set --vault-name <name> --name "OPENAI-API-KEY"   --value "..."
#   az keyvault secret set --vault-name <name> --name "SENDGRID-API-KEY" --value "..."
#
# Keeping secrets out of Terraform state prevents them appearing in
# the tfstate file stored in blob storage.
# ─────────────────────────────────────────────────────────────────────────────
