# modules/app-registrations/outputs.tf

output "tenant_id" {
  description = "Azure AD tenant ID"
  value       = data.azuread_client_config.current.tenant_id
}

output "api_client_id" {
  description = "API app registration client ID → VITE_API_CLIENT_ID"
  value       = azuread_application.api.client_id
}

output "api_scope" {
  description = "API scope URI → VITE_API_SCOPE"
  value       = "api://${azuread_application.api.client_id}/access_as_user"
}

output "frontend_client_id" {
  description = "SPA app registration client ID → VITE_CLIENT_ID"
  value       = azuread_application.frontend.client_id
}
