# modules/service-bus/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — must be passed explicitly; no default to avoid silent region mismatch"
  type        = string
}

variable "namespace_name" {
  description = "Service Bus namespace name — globally unique, 6-50 chars, alphanumeric and hyphens only"
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{4,48}[a-zA-Z0-9]$", var.namespace_name))
    error_message = "Service Bus namespace name must be 6-50 chars, start with a letter, and contain only letters, numbers, or hyphens."
  }
}

variable "sku" {
  description = "Service Bus SKU. Standard supports Topics/Subscriptions (no private endpoints). Premium adds VNet integration and private endpoints — required for prod network isolation."
  type        = string
  default     = "Standard"

  validation {
    condition     = contains(["Basic", "Standard", "Premium"], var.sku)
    error_message = "sku must be Basic, Standard, or Premium. Note: Basic does not support Topics."
  }
}

variable "max_delivery_count" {
  description = "Maximum delivery attempts before a message is dead-lettered"
  type        = number
  default     = 5
}

# ── Private endpoint (Premium SKU only) ──────────────────────────────────────
# Leave empty for Standard SKU — no private endpoint will be created.
variable "private_endpoint_subnet_id" {
  description = "Subnet ID for the private endpoint (subnet-privatelinks). Required only when sku = Premium."
  type        = string
  default     = ""
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for privatelink.servicebus.windows.net. Required only when sku = Premium. The networking module must be updated to create this zone."
  type        = string
  default     = ""
}

# ── RBAC ──────────────────────────────────────────────────────────────────────
variable "sender_principal_ids" {
  description = "Map of descriptive label → principal ID for identities that need Azure Service Bus Data Sender on the award-events topic. Map keys must be static strings (e.g. 'aca-primary') so Terraform can plan for_each without knowing the principal ID at plan time."
  type        = map(string)
  default     = {}
}

variable "receiver_principal_ids" {
  description = "Map of descriptive label → principal ID for identities that need Azure Service Bus Data Receiver on the award-events topic. Map keys must be static strings (e.g. 'auxiliary-function') so Terraform can plan for_each when the identity is created in the same apply."
  type        = map(string)
  default     = {}
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
