# modules/payout-orchestrator/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region for all resources in this module"
  type        = string
}

variable "function_app_name" {
  description = "Container App name (was Function App name). Convention: award-payout-orchestrator-{env}"
  type        = string
}

variable "storage_account_name" {
  description = "Storage account name for Durable Functions state (Table/Queue/Blob). Max 24 chars, lowercase alphanumeric only."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "Storage account name must be 3-24 lowercase alphanumeric characters."
  }
}

variable "environment" {
  description = "Deployment environment label (sandbox, dev, prod)"
  type        = string
}

# ── Container App Environment ─────────────────────────────────────────────────

variable "container_app_environment_id" {
  description = "ID of the Container App Environment to deploy into (primary CAE)"
  type        = string
}

# ── Managed identity ──────────────────────────────────────────────────────────

variable "identity_id" {
  description = "Resource ID of the pre-created User-Assigned Managed Identity"
  type        = string
}

variable "identity_client_id" {
  description = "Client ID of the pre-created User-Assigned Managed Identity — injected as AZURE_CLIENT_ID so DefaultAzureCredential picks the correct MI"
  type        = string
}

# ── ACR ───────────────────────────────────────────────────────────────────────

variable "acr_login_server" {
  description = "Container Registry login server hostname"
  type        = string
}

variable "acr_admin_username" {
  description = "Container Registry admin username"
  type        = string
  sensitive   = true
}

variable "acr_admin_password" {
  description = "Container Registry admin password"
  type        = string
  sensitive   = true
}

# ── Service Bus ───────────────────────────────────────────────────────────────

variable "service_bus_fqns" {
  description = "Fully-qualified Service Bus namespace hostname (e.g. sb-award-sandbox.servicebus.windows.net)"
  type        = string
}

variable "service_bus_topic_name" {
  description = "Service Bus topic name (award-events)"
  type        = string
}

variable "service_bus_subscription_name" {
  description = "Service Bus subscription the orchestrator consumes (payout-orchestrator)"
  type        = string
}

# ── Downstream services ───────────────────────────────────────────────────────

variable "award_api_base_url" {
  description = "Base URL of the Award Nomination API — used to PATCH nomination status (e.g. https://award-api-sandbox.azurecontainerapps.io)"
  type        = string
}

variable "workday_proxy_url" {
  description = "Base URL of the Workday_Proxy ACA (e.g. https://workday-proxy-sandbox.azurecontainerapps.io). Set to \"\" on first deploy; wire up after both services are running."
  type        = string
  default     = ""
}

# ── Observability ─────────────────────────────────────────────────────────────

variable "appinsights_connection_string" {
  description = "Application Insights connection string for distributed tracing"
  type        = string
  sensitive   = true
}

# ── Scale ─────────────────────────────────────────────────────────────────────

variable "min_replicas" {
  description = "Minimum replica count. Keep at 1 — Functions host manages its own Service Bus polling; KEDA scale-to-zero requires TriggerAuthentication not yet in azurerm provider."
  type        = number
  default     = 1
}

variable "max_replicas" {
  description = "Maximum replica count."
  type        = number
  default     = 2
}

variable "cpu" {
  description = "CPU allocation per replica"
  type        = number
  default     = 0.25
}

variable "memory" {
  description = "Memory allocation per replica"
  type        = string
  default     = "0.5Gi"
}

# ── Tags ──────────────────────────────────────────────────────────────────────

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
