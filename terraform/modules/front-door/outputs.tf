# modules/front-door/outputs.tf

output "afd_profile_id" {
  description = "AFD profile resource ID"
  value       = azurerm_cdn_frontdoor_profile.afd.id
}

output "afd_endpoint_hostname" {
  description = "Public AFD hostname — e.g. award-nomination-api.azurefd.net"
  value       = azurerm_cdn_frontdoor_endpoint.endpoint.host_name
}

output "afd_endpoint_id" {
  description = "AFD endpoint resource ID — consumed by static-web-app module"
  value       = azurerm_cdn_frontdoor_endpoint.endpoint.id
}

output "waf_policy_id" {
  description = "WAF policy resource ID"
  value       = azurerm_cdn_frontdoor_firewall_policy.waf.id
}

# ─────────────────────────────────────────────────────────────────────────────
# POST-DEPLOY MANUAL STEP — Private Link approval
# ─────────────────────────────────────────────────────────────────────────────
# After terraform apply, AFD creates a pending private endpoint connection
# to each CAE. These MUST be approved manually before traffic flows.
#
# Approve via az cli:
#   # Get the pending connection name for East CAE
#   az containerapp env show \
#     --name cae-award-eastus \
#     --resource-group rg_award_nomination \
#     --query "properties.privateEndpointConnections" -o table
#
#   # Approve it
#   az network private-endpoint-connection approve \
#     --resource-group rg_award_nomination \
#     --name <connection-name> \
#     --resource-name cae-award-eastus \
#     --type Microsoft.App/managedEnvironments \
#     --description "Approved AFD Private Link"
#
# Repeat for West CAE (cae-award-westus).
# Until approved, AFD returns 502 Bad Gateway.
# ─────────────────────────────────────────────────────────────────────────────
