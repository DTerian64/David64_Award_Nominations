# modules/service-bus/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Service Bus — event broker for award lifecycle events
#
# Creates:
#   - Service Bus Namespace (Standard or Premium SKU)
#   - Topic:        award-events  (all award lifecycle events published here)
#   - Subscription: email-processor  (consumed by the Auxiliary Function)
#   - RBAC role assignments — sender identities (backend Container Apps)
#   - RBAC role assignments — receiver identity (Auxiliary Function)
#   - Private endpoint → subnet-privatelinks  (Premium SKU only)
#
# Authentication model:
#   local_auth_enabled = false enforces Managed Identity / Entra ID for all
#   access. SAS keys and connection strings are disabled entirely.
#   All callers must hold an appropriate RBAC role on the namespace.
#
# SKU note:
#   Standard — supports Topics/Subscriptions; no VNet integration or private
#              endpoints. Suitable for dev and sandbox.
#   Premium  — adds VNet service endpoints, private endpoints, and dedicated
#              capacity. Recommended for prod when network isolation is required.
#              Requires private_endpoint_subnet_id and private_dns_zone_id,
#              and the networking module must include the
#              privatelink.servicebus.windows.net private DNS zone.
#
# Extending to new event types:
#   To add a new consumer (e.g. payroll-processor), add a new
#   azurerm_servicebus_subscription resource and a corresponding
#   azurerm_role_assignment for its identity. No changes needed to the
#   publisher (backend API) or to the topic itself.
# ─────────────────────────────────────────────────────────────────────────────

# ── Namespace ─────────────────────────────────────────────────────────────────
resource "azurerm_servicebus_namespace" "sb" {
  name                = var.namespace_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = var.sku

  # Enforce TLS 1.2 minimum
  minimum_tls_version = "1.2"

  # Disable local (SAS key) auth — all access must use Managed Identity / RBAC.
  # This means no connection strings are ever issued or stored.
  local_auth_enabled = false

  tags = var.tags
}

# ── Topic — award-events ──────────────────────────────────────────────────────
# Single topic for all award lifecycle events.
# Consumers add subscriptions here — the publisher never changes.
resource "azurerm_servicebus_topic" "award_events" {
  name         = "award-events"
  namespace_id = azurerm_servicebus_namespace.sb.id

  # Retain unprocessed messages for 7 days — long enough to survive a weekend
  # outage with time to recover before messages expire.
  default_message_ttl = "P7D"

  # Batching improves throughput at no cost for our message volumes.
  batched_operations_enabled = true

  # Ordering is not required — email delivery order is not significant.
  support_ordering = false
}

# ── Subscription — email-processor ───────────────────────────────────────────
# Consumed exclusively by the Award Auxiliary Function.
# Each message received is locked for lock_duration; if the Function crashes
# or times out, the lock expires and the message is requeued automatically.
# After max_delivery_count failures the message is dead-lettered.
resource "azurerm_servicebus_subscription" "email_processor" {
  name     = "email-processor"
  topic_id = azurerm_servicebus_topic.award_events.id

  # Max 5 delivery attempts before dead-lettering.
  max_delivery_count = var.max_delivery_count

  # Lock duration — how long the Function holds the message before Azure
  # assumes it has failed and requeues it. 5 minutes is generous for
  # SMTP delivery but short enough to recover quickly from crashes.
  lock_duration = "PT5M"

  # Dead-letter messages that exceed their TTL, not just delivery count.
  dead_lettering_on_message_expiration = true

  # Match topic TTL — no point keeping messages longer than the topic does.
  default_message_ttl = "P7D"
}

# ── Private endpoint (Premium SKU only) ───────────────────────────────────────
# Standard SKU does not support private endpoints. Set sku = "Premium" and
# provide private_endpoint_subnet_id + private_dns_zone_id in prod if
# network isolation is required.
#
# Prerequisite: the networking module must create the private DNS zone
# privatelink.servicebus.windows.net and output its ID as
# dns_zone_servicebus_id before this block is enabled.
resource "azurerm_private_endpoint" "sb" {
  count = var.sku == "Premium" && var.private_endpoint_subnet_id != "" ? 1 : 0

  name                = "pe-${var.namespace_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-${var.namespace_name}"
    private_connection_resource_id = azurerm_servicebus_namespace.sb.id
    subresource_names              = ["namespace"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "dns-group-sb"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }
}

# ── RBAC — sender identities (backend Container Apps) ─────────────────────────
# Azure Service Bus Data Sender scoped to the topic — least privilege.
# Senders can only publish to award-events; they cannot read or manage.
#
# Uses map(string) rather than list — Terraform requires static keys in for_each
# when values are only known after apply (e.g. a newly-created managed identity).
# Keys are descriptive static strings; values are the principal IDs.
resource "azurerm_role_assignment" "sender" {
  for_each = var.sender_principal_ids

  scope                = azurerm_servicebus_topic.award_events.id
  role_definition_name = "Azure Service Bus Data Sender"
  principal_id         = each.value
}

# ── RBAC — receiver identities (Auxiliary Function) ───────────────────────────
# Azure Service Bus Data Receiver scoped to the topic — least privilege.
# Receivers can read from subscriptions of award-events but cannot publish.
resource "azurerm_role_assignment" "receiver" {
  for_each = var.receiver_principal_ids

  scope                = azurerm_servicebus_topic.award_events.id
  role_definition_name = "Azure Service Bus Data Receiver"
  principal_id         = each.value
}
