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

output "post_deploy_checklist" {
  description = "Steps to complete after terraform apply"
  value       = <<-EOT

  Dev environment deployed. Complete these steps:

  1. KV secrets are auto-wired from module outputs (storage key, OpenAI key/endpoint,
     SQL server/database). Remaining secrets (SQL-USER, SQL-PASSWORD, GMAIL-APP-PASSWORD,
     etc.) must be present in terraform.tfvars secrets map before apply.

  2. KV access policies are fully automated via User-Assigned Managed Identities —
     no manual two-pass apply required. Both MIs are created before Container Apps.

  3. Run mid-terraform.ps1 (Pass 2 prep):
     Patches swa_redirect_urls + cors_allowed_origins in terraform.tfvars and sets
     AZURE_STATIC_WEB_APPS_API_TOKEN secret in the GitHub 'development' environment.
     VITE_* are managed automatically as SWA app_settings — no GitHub vars needed.

  4. Run Pass 2: terraform plan / apply, then .\post-terraform.ps1

  5. Create dev branch and trigger first deploy:
     git checkout -b dev
     git push origin dev

  EOT
}
