# outputs.tf
# ─────────────────────────────────────────────────────────────────────────────
# Key outputs and post-apply manual steps
# ─────────────────────────────────────────────────────────────────────────────

output "vnet_east_id" {
  description = "East US VNet ID — needed when recreating CAE East"
  value       = azurerm_virtual_network.east.id
}

output "vnet_west_id" {
  description = "West US VNet ID — needed when recreating CAE West"
  value       = azurerm_virtual_network.west.id
}

output "subnet_aca_east_id" {
  description = "East ACA subnet ID — use when recreating cae-award-eastus"
  value       = azurerm_subnet.aca_east.id
}

output "subnet_aca_west_id" {
  description = "West ACA subnet ID — use when recreating cae-award-westus"
  value       = azurerm_subnet.aca_west.id
}

output "private_endpoint_ips" {
  description = "Private IP addresses assigned to each private endpoint"
  value = {
    sql    = azurerm_private_endpoint.sql.private_service_connection[0].private_ip_address
    blob   = azurerm_private_endpoint.blob.private_service_connection[0].private_ip_address
    kv     = azurerm_private_endpoint.kv.private_service_connection[0].private_ip_address
    openai = azurerm_private_endpoint.openai.private_service_connection[0].private_ip_address
    acr    = azurerm_private_endpoint.acr.private_service_connection[0].private_ip_address
  }
}

output "kv_lockdown_commands" {
  description = "Run these commands after terraform apply to lock down Key Vault"
  value = <<-EOT

  # Key Vault lockdown
  az keyvault update \
    --name kv-awardnominations \
    --resource-group rg_award_nomination \
    --default-action Deny \
    --bypass AzureServices

  %{for ip in var.my_ips~}
  az keyvault network-rule add \
    --name kv-awardnominations \
    --resource-group rg_award_nomination \
    --ip-address ${ip}
  %{endfor~}

  EOT
}

output "openai_lockdown_commands" {
  description = "Run these commands after terraform apply to lock down Azure OpenAI"
  value = <<-EOT

  # Azure OpenAI lockdown
  %{for ip in var.my_ips~}
  az cognitiveservices account network-rule add \
    --name award-nomination-open-AI \
    --resource-group rg_award_nomination \
    --ip-address ${ip}
  %{endfor~}

  EOT
}

output "next_steps" {
  description = "What to do after terraform apply"
  value = <<-EOT

  Terraform apply complete. Next steps:

  1. Verify private endpoints are approved:
     az network private-endpoint list -g rg_award_nomination -o table

  2. Run KV lockdown:
     terraform output kv_lockdown_commands

  3. Run OpenAI lockdown:
     terraform output openai_lockdown_commands

  4. Recreate CAEs as internal — run cae-recreation.sh

  5. Test connectivity before disabling public SQL access

  EOT
}
