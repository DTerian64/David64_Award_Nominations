# modules/static-web-app/outputs.tf

output "static_web_app_id" {
  description = "Static Web App resource ID"
  value       = azurerm_static_web_app.frontend.id
}

output "default_hostname" {
  description = "Default SWA hostname — e.g. purple-sand-abc123.azurestaticapps.net"
  value       = azurerm_static_web_app.frontend.default_host_name
}

output "api_key" {
  description = "Deployment token — add to GitHub repo secret AZURE_STATIC_WEB_APPS_API_TOKEN"
  value       = azurerm_static_web_app.frontend.api_key
  sensitive   = true
}

# ─────────────────────────────────────────────────────────────────────────────
# POST-DEPLOY NOTE — GitHub Actions deployment token
# ─────────────────────────────────────────────────────────────────────────────
# When deploying to a NEW subscription, the SWA gets a new deployment token.
# Update your GitHub repo secret with the new token:
#
#   # Get the token
#   terraform output -raw static_web_app_api_key
#
#   # Set it in GitHub (requires gh cli)
#   gh secret set AZURE_STATIC_WEB_APPS_API_TOKEN \
#     --repo DTerian64/David64_Award_Nominations \
#     --body "$(terraform output -raw static_web_app_api_key)"
#
# Without this update, GitHub Actions will deploy to the OLD SWA
# in the old subscription, not the new one.
# ─────────────────────────────────────────────────────────────────────────────
