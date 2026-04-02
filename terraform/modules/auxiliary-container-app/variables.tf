# modules/auxiliary-container-app/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — must be passed explicitly; no default to avoid silent region mismatch"
  type        = string
}

variable "app_name" {
  description = "Container App name — globally unique within the CAE. Convention: award-auxiliary-{env}"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, sandbox, prod) — injected as ENVIRONMENT env var"
  type        = string
}

# ── Container App Environment ─────────────────────────────────────────────────
variable "container_app_environment_id" {
  description = "Resource ID of the existing Container App Environment (CAE) to deploy into. Use the primary CAE — the worker does not need multi-region replication."
  type        = string
}

# ── Managed Identity ──────────────────────────────────────────────────────────
# The identity must be created BEFORE this module runs (in the environment
# main.tf) so that Key Vault access policies and Service Bus RBAC assignments
# can be granted before Azure validates them at Container App creation time.
variable "auxiliary_identity_id" {
  description = "Resource ID of the User-Assigned Managed Identity for the auxiliary worker. Must be pre-created and pre-authorized for KV and Service Bus."
  type        = string
}

variable "auxiliary_identity_client_id" {
  description = "Client ID (appId) of the User-Assigned Managed Identity. Required by DefaultAzureCredential (AZURE_CLIENT_ID) and by KEDA for workload identity authentication against Service Bus."
  type        = string
}

# ── ACR — image registry ──────────────────────────────────────────────────────
variable "acr_login_server" {
  description = "ACR login server URL (e.g. acrawardnomination.azurecr.io)"
  type        = string
}

variable "acr_admin_username" {
  description = "ACR admin username"
  type        = string
  sensitive   = true
}

variable "acr_admin_password" {
  description = "ACR admin password"
  type        = string
  sensitive   = true
}

# ── Service Bus ───────────────────────────────────────────────────────────────
variable "service_bus_fqns" {
  description = "Service Bus namespace FQDN (e.g. sb-award-sandbox.servicebus.windows.net). Used as SERVICE_BUS_FQNS env var and as fullyQualifiedNamespace in the KEDA scaler metadata."
  type        = string
}

variable "service_bus_topic_name" {
  description = "Service Bus topic name (e.g. award-events). Used as SERVICE_BUS_TOPIC_NAME env var and in the KEDA scaler metadata."
  type        = string
}

variable "service_bus_subscription_name" {
  description = "Service Bus subscription name (e.g. email-processor). Used as SERVICE_BUS_SUBSCRIPTION_NAME env var and in the KEDA scaler metadata."
  type        = string
}

# ── KEDA scaling ──────────────────────────────────────────────────────────────
variable "keda_message_count" {
  description = "Number of messages per replica that KEDA targets. One replica is activated when pending messages >= this value. Set low (e.g. 5) for fast scale-up; set higher to reduce replica churn."
  type        = number
  default     = 5
}

variable "min_replicas" {
  description = "Minimum replica count. 0 = scale to zero (recommended for dev/sandbox — no cost when idle). 1 = always-on (recommended for prod — eliminates cold-start latency)."
  type        = number
  default     = 0
}

variable "max_replicas" {
  description = "Maximum replica count. Controls burst capacity and cost ceiling. Recommended: 1 for dev, 2 for sandbox, 5 for prod."
  type        = number
  default     = 1
}

# ── Container sizing ──────────────────────────────────────────────────────────
# Worker tasks (SMTP calls, DB reads) are I/O-bound, not CPU-bound.
# 0.25 CPU / 0.5Gi is sufficient for dev and sandbox. Increase for prod if
# email volume grows or handlers do CPU-intensive work (e.g. PDF generation).
variable "cpu" {
  description = "CPU allocation per container replica (vCPU)"
  type        = number
  default     = 0.25
}

variable "memory" {
  description = "Memory allocation per container replica"
  type        = string
  default     = "0.5Gi"
}

# ── Key Vault ─────────────────────────────────────────────────────────────────
variable "key_vault_uri" {
  description = "Key Vault URI (e.g. https://kv-award-sandbox.vault.azure.net/). Injected as KEY_VAULT_URL and used to resolve kv_secret_references."
  type        = string
}

# ── Environment variables (non-secret) ────────────────────────────────────────
# Non-secret config values passed as plain environment variables.
# For secrets, use kv_secret_references — values never touch Terraform state.
variable "environment_variables" {
  description = "Additional non-secret environment variables to inject into the container. Built-in vars (SERVICE_BUS_FQNS, KEY_VAULT_URL, ENVIRONMENT, AZURE_CLIENT_ID, OTEL_SERVICE_NAME) are always set by the module."
  type = list(object({
    name  = string
    value = string
  }))
  default = []
}

# ── Key Vault secret references ───────────────────────────────────────────────
# Three-tier chain: KV secret → ACA secret reference → env var
#   1. KV holds the value (fetched at runtime by the managed identity)
#   2. ACA secret block references the KV secret URI (value never in state)
#   3. Env var in the container references the ACA secret name
#
# Convention:
#   kv_secret_name  UPPER-HYPHEN  e.g. "SQL-PASSWORD"
#   ACA secret name lower(kv_secret_name)  e.g. "sql-password"
#   env_name        app env var           e.g. "SQL_PASSWORD"
variable "kv_secret_references" {
  description = "Secrets to pull from Key Vault and expose as env vars via ACA secret references. Values are never stored in Terraform state — resolved at container startup via managed identity."
  type = list(object({
    env_name       = string  # env var name the app reads:  "SQL_PASSWORD"
    kv_secret_name = string  # Key Vault secret name:       "SQL-PASSWORD"
  }))
  default = []
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
