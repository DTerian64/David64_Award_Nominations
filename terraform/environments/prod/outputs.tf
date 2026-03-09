# environments/prod/outputs.tf
# ─────────────────────────────────────────────────────────────────────────────
# Key values to note after terraform apply
# Run: terraform output
# ─────────────────────────────────────────────────────────────────────────────

output "app_url" {
  description = "Public URL for the application"
  value       = "https://${module.front_door.afd_endpoint_hostname}"
}

output "frontend_url" {
  description = "Static Web App public URL"
  value       = "https://${module.static_web_app.default_hostname}"
}

output "grafana_url" {
  description = "Grafana dashboard URL"
  value       = module.grafana.grafana_endpoint
}

output "key_vault_uri" {
  description = "Key Vault URI — add secrets here after deploy"
  value       = module.key_vault.vault_uri
}

output "acr_login_server" {
  description = "ACR login server — update GitHub Actions workflows with this"
  value       = module.container_registry.login_server
}

output "swa_deployment_token" {
  description = "SWA deployment token — update GitHub secret SWA_TOKEN_PROD"
  value       = module.static_web_app.api_key
  sensitive   = true
}

output "aca_east_principal_id" {
  description = "East User-Assigned Managed Identity principal ID (for reference)"
  value       = azurerm_user_assigned_identity.aca_east.principal_id
}

output "aca_west_principal_id" {
  description = "West User-Assigned Managed Identity principal ID (for reference)"
  value       = azurerm_user_assigned_identity.aca_west.principal_id
}

output "post_deploy_checklist" {
  description = "Steps to complete after terraform apply"
  value       = <<-EOT

  ✅ Terraform apply complete. Complete these steps:

  1. KV secrets are auto-wired from module outputs (storage key, OpenAI key/endpoint,
     SQL server/database). Remaining secrets (SQL-USER, SQL-PASSWORD, GMAIL-APP-PASSWORD,
     etc.) must be present in terraform.tfvars secrets map before apply.

  2. KV access policies are fully automated via User-Assigned Managed Identities —
     no manual two-pass apply required. Both MIs are created before Container Apps.

  3. Approve AFD Private Link connections (if using Premium tier):
     Portal → cae-award-eastus-prod → Networking → Private endpoint connections → Approve
     Portal → cae-award-westus-prod → Networking → Private endpoint connections → Approve

  4. Update GitHub secrets:
     SWA_TOKEN_PROD:    terraform output -raw swa_deployment_token
     ACR_NAME_PROD:     terraform output -raw acr_login_server

  5. Run KV and OpenAI lockdown commands:
     terraform output kv_lockdown_commands
     terraform output openai_lockdown_commands

  6. Trigger first GitHub Actions deployment:
     git commit --allow-empty -m "trigger prod deploy"
     git push origin main

  EOT
}
