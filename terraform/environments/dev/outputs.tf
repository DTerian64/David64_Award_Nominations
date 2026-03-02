# environments/dev/outputs.tf

output "app_url" {
  description = "Public URL for the dev application"
  value       = "https://${module.front_door.afd_endpoint_hostname}"
}

output "frontend_url" {
  description = "Static Web App public URL"
  value       = "https://${module.static_web_app.default_hostname}"
}

output "key_vault_uri" {
  description = "Key Vault URI — add secrets here after deploy"
  value       = module.key_vault.vault_uri
}

output "acr_login_server" {
  description = "ACR login server — update GitHub Actions workflows"
  value       = module.container_registry.login_server
}

output "swa_deployment_token" {
  description = "SWA deployment token — update GitHub secret SWA_TOKEN_DEV"
  value       = module.static_web_app.api_key
  sensitive   = true
}

output "aca_east_principal_id" {
  description = "East ACA managed identity — add to aca_principal_ids after first apply"
  value       = module.container_apps.east_principal_id
}

output "aca_west_principal_id" {
  description = "West ACA managed identity — add to aca_principal_ids after first apply"
  value       = module.container_apps.west_principal_id
}

output "post_deploy_checklist" {
  description = "Steps to complete after terraform apply"
  value       = <<-EOT

  Dev environment deployed. Complete these steps:

  1. Add KV secrets:
     az keyvault secret set --vault-name kv-awardnominations-dev --name "DB-PASSWORD"      --value "..."
     az keyvault secret set --vault-name kv-awardnominations-dev --name "OPENAI-API-KEY"   --value "..."
     az keyvault secret set --vault-name kv-awardnominations-dev --name "SENDGRID-API-KEY" --value "..."

  2. Update KV aca_principal_ids and re-apply:
     terraform output aca_east_principal_id
     terraform output aca_west_principal_id

  3. Approve AFD Private Link in portal for both CAEs

  4. Update GitHub secret SWA_TOKEN_DEV:
     terraform output -raw swa_deployment_token

  5. Create dev branch and trigger first deploy:
     git checkout -b dev
     git push origin dev

  EOT
}
