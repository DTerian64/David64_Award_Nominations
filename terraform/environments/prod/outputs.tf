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
  description = "East ACA managed identity — add to key_vault aca_principal_ids after first apply"
  value       = module.container_apps.east_principal_id
}

output "aca_west_principal_id" {
  description = "West ACA managed identity — add to key_vault aca_principal_ids after first apply"
  value       = module.container_apps.west_principal_id
}

output "post_deploy_checklist" {
  description = "Steps to complete after terraform apply"
  value       = <<-EOT

  ✅ Terraform apply complete. Complete these steps:

  1. Add KV secrets:
     terraform output key_vault_uri
     az keyvault secret set --vault-name <name> --name "DB-PASSWORD"      --value "..."
     az keyvault secret set --vault-name <name> --name "OPENAI-API-KEY"   --value "..."
     az keyvault secret set --vault-name <name> --name "SENDGRID-API-KEY" --value "..."

  2. Update KV access policy with ACA managed identities:
     terraform output aca_east_principal_id
     terraform output aca_west_principal_id
     Then add both to aca_principal_ids in terraform.tfvars and re-apply

  3. Approve AFD Private Link connections:
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
