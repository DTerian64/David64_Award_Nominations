# modules/application-insights/outputs.tf

# ── Backend (FastAPI) ─────────────────────────────────────────────────────────
output "backend_connection_string" {
  description = "App Insights connection string for the FastAPI backend — store in Key Vault"
  value       = azurerm_application_insights.backend.connection_string
  sensitive   = true
}

output "backend_instrumentation_key" {
  description = "Legacy instrumentation key — prefer connection_string for new SDKs"
  value       = azurerm_application_insights.backend.instrumentation_key
  sensitive   = true
}

output "backend_app_id" {
  description = "App Insights application ID — used for cross-resource queries in Log Analytics"
  value       = azurerm_application_insights.backend.app_id
}

# ── Frontend (React/Vite) ─────────────────────────────────────────────────────
output "frontend_connection_string" {
  description = "App Insights connection string for the React frontend — passed as VITE_APPINSIGHTS_CONNECTION_STRING"
  value       = azurerm_application_insights.frontend.connection_string
  sensitive   = true
}

output "frontend_instrumentation_key" {
  description = "Legacy instrumentation key for the frontend — prefer connection_string for new SDKs"
  value       = azurerm_application_insights.frontend.instrumentation_key
  sensitive   = true
}

output "frontend_app_id" {
  description = "App Insights application ID for the frontend"
  value       = azurerm_application_insights.frontend.app_id
}
