# environments/sandbox/outputs.tf

output "app_url" {
  description = "Public URL for the sandbox application"
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

output "vite_client_id" {
  description = "Frontend SPA client ID → VITE_CLIENT_ID GitHub env var"
  value       = module.app_registrations.frontend_client_id
}

output "vite_tenant_id" {
  description = "Azure AD tenant ID → VITE_TENANT_ID GitHub env var"
  value       = module.app_registrations.tenant_id
}

output "vite_api_scope" {
  description = "API scope URI → VITE_API_SCOPE GitHub env var"
  value       = module.app_registrations.api_scope
}

output "vite_api_client_id" {
  description = "API app client ID → VITE_API_CLIENT_ID GitHub env var"
  value       = module.app_registrations.api_client_id
}

output "acr_name" {
  description = "ACR registry name — used by GitHub Actions (ACR_NAME var)"
  value       = module.container_registry.acr_name
}

output "container_app_primary" {
  description = "Primary Container App name — GitHub Actions CONTAINER_APP_EASTUS"
  value       = var.app_name_primary
}

output "container_app_secondary" {
  description = "Secondary Container App name — GitHub Actions CONTAINER_APP_WESTUS"
  value       = var.app_name_secondary
}

output "resource_group_name" {
  description = "Sandbox resource group name — GitHub Actions RESOURCE_GROUP"
  value       = var.resource_group_name
}

output "frontdoor_profile" {
  description = "Front Door profile name — GitHub Actions FRONTDOOR_PROFILE"
  value       = var.afd_profile_name
}

output "frontdoor_endpoint" {
  description = "Front Door endpoint name — GitHub Actions FRONTDOOR_ENDPOINT"
  value       = var.afd_endpoint_name
}

output "post_deploy_checklist" {
  description = "Steps to complete after terraform apply"
  value       = <<-EOT

  Sandbox environment deployed. Complete these steps:

  1. KV secrets are auto-wired from module outputs (storage key, OpenAI key/endpoint,
     SQL server/database). Remaining secrets (SQL-USER, SQL-PASSWORD, GMAIL-APP-PASSWORD,
     etc.) must be present in terraform.tfvars secrets map before apply.

  2. KV access policies are fully automated via User-Assigned Managed Identities —
     no manual two-pass apply required. Both MIs are created before Container Apps.

  3. Run mid-terraform.ps1 (Pass 2 prep):
     Patches swa_redirect_urls + cors_allowed_origins in terraform.tfvars and sets
     AZURE_STATIC_WEB_APPS_API_TOKEN secret in the GitHub 'sandbox' environment.
     AZURE_STATIC_WEB_APPS_API_TOKEN secret and VITE_* variables in the GitHub
     'sandbox' environment (build-time, passed via workflow env: block).

  4. Run Pass 2: terraform plan / apply, then .\post-terraform.ps1

  5. Create sandbox branch and trigger first deploy:
     git checkout -b sandbox
     git push origin sandbox

  EOT
}
