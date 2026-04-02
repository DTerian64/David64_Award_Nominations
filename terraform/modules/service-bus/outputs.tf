# modules/service-bus/outputs.tf

output "namespace_id" {
  description = "Service Bus namespace resource ID"
  value       = azurerm_servicebus_namespace.sb.id
}

output "namespace_name" {
  description = "Service Bus namespace name"
  value       = azurerm_servicebus_namespace.sb.name
}

output "namespace_fqns" {
  description = "Fully-qualified namespace hostname — inject as SERVICE_BUS_FQNS env var. Used by the backend (publisher) and Auxiliary Function (receiver) with DefaultAzureCredential. Format: <namespace>.servicebus.windows.net"
  value       = "${azurerm_servicebus_namespace.sb.name}.servicebus.windows.net"
}

output "topic_name" {
  description = "Name of the award-events topic"
  value       = azurerm_servicebus_topic.award_events.name
}

output "email_processor_subscription_name" {
  description = "Name of the email-processor subscription — used in Auxiliary Function trigger binding"
  value       = azurerm_servicebus_subscription.email_processor.name
}

# ─────────────────────────────────────────────────────────────────────────────
# POST-DEPLOY NOTES
# ─────────────────────────────────────────────────────────────────────────────
# 1. Verify local_auth_enabled = false took effect (SAS keys should be absent):
#      az servicebus namespace show \
#        --name <namespace> \
#        --resource-group <rg> \
#        --query "properties.disableLocalAuth"
#    Expected: true
#
# 2. Verify RBAC assignments:
#      az role assignment list --scope <topic-resource-id> --output table
#
# 3. Monitor dead-letter queue:
#      az servicebus topic subscription show \
#        --name email-processor \
#        --topic-name award-events \
#        --namespace-name <namespace> \
#        --resource-group <rg> \
#        --query "properties.countDetails.deadLetterMessageCount"
# ─────────────────────────────────────────────────────────────────────────────
