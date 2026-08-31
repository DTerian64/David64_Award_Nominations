# modules/payroll-broker/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — must be passed explicitly; no default to avoid silent region mismatch"
  type        = string
}

variable "app_name" {
  description = "Container App name — globally unique within the CAE. Convention: award-payroll-broker-{env}"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, sandbox, prod) — injected as ENVIRONMENT env var"
  type        = string
}

# ── Container App Environment ─────────────────────────────────────────────────
variable "container_app_environment_id" {
  description = "Resource ID of the existing primary Container App Environment (CAE) to deploy into"
  type        = string
}

# ── Managed Identity ──────────────────────────────────────────────────────────
# Must be created BEFORE this module runs (in the environment main.tf) so that
# Key Vault access policies and Service Bus RBAC assignments can be granted
# before Azure validates them at Container App creation time.
variable "identity_id" {
  description = "Resource ID of the User-Assigned Managed Identity for the payroll broker. Must be pre-created and pre-authorized for KV (Get/List) and Service Bus (Sender + Receiver)."
  type        = string
}

variable "identity_client_id" {
  description = "Client ID (appId) of the User-Assigned Managed Identity. Required by DefaultAzureCredential (AZURE_CLIENT_ID) and by KEDA for workload identity auth against Service Bus."
  type        = string
}

# ── ACR ───────────────────────────────────────────────────────────────────────
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
  description = "Service Bus namespace FQDN (e.g. sb-award-sandbox.servicebus.windows.net)"
  type        = string
}

variable "service_bus_topic_name" {
  description = "Service Bus topic name (award-events)"
  type        = string
}

variable "service_bus_subscription_name" {
  description = "Service Bus subscription the broker consumes (payroll-processor)"
  type        = string
}

# ── HTTP ingress ──────────────────────────────────────────────────────────────
variable "container_port" {
  description = "Port the payroll-broker HTTP server listens on inside the container"
  type        = number
  default     = 8000
}

# ── KEDA scaling ──────────────────────────────────────────────────────────────
variable "keda_message_count" {
  description = "Target messages per replica for KEDA scale-up. One additional replica is activated when pending messages >= this value above the baseline."
  type        = number
  default     = 5
}

variable "min_replicas" {
  description = "Minimum replica count. Must be >= 1 for the payroll broker — the HTTP webhook endpoint must always be live to receive provider callbacks. Scale-to-zero (0) would cause missed webhook deliveries."
  type        = number
  default     = 1

  validation {
    condition     = var.min_replicas >= 1
    error_message = "min_replicas must be at least 1 for the payroll broker to keep the HTTP webhook endpoint alive."
  }
}

variable "max_replicas" {
  description = "Maximum replica count. Controls burst capacity and cost ceiling."
  type        = number
  default     = 2
}

# ── Container sizing ──────────────────────────────────────────────────────────
# Payroll broker is I/O-bound (outbound HTTP to Gusto, Service Bus reads).
# 0.5 CPU / 1Gi is sufficient; increase if concurrent payroll volume grows.
variable "cpu" {
  description = "CPU allocation per container replica (vCPU)"
  type        = number
  default     = 0.5
}

variable "memory" {
  description = "Memory allocation per container replica"
  type        = string
  default     = "1Gi"
}

# ── Key Vault ─────────────────────────────────────────────────────────────────
variable "key_vault_uri" {
  description = "Key Vault URI (e.g. https://kv-award-sandbox.vault.azure.net/). Injected as KEY_VAULT_URL and used to resolve kv_secret_references."
  type        = string
}

# ── Environment variables (non-secret) ────────────────────────────────────────
variable "environment_variables" {
  description = "Additional non-secret environment variables to inject. Built-in vars (SERVICE_BUS_*, KEY_VAULT_URL, ENVIRONMENT, AZURE_CLIENT_ID, OTEL_SERVICE_NAME) are always set by the module."
  type = list(object({
    name  = string
    value = string
  }))
  default = []
}

# ── Key Vault secret references ───────────────────────────────────────────────
# Convention: kv_secret_name UPPER-HYPHEN (e.g. "GUSTO-CLIENT-SECRET")
#             ACA secret name = lower(kv_secret_name)  (e.g. "gusto-client-secret")
#             env_name = app env var (e.g. "GUSTO_CLIENT_SECRET")
variable "kv_secret_references" {
  description = "Secrets to pull from Key Vault and expose as env vars. Values never stored in Terraform state — resolved at container startup via managed identity."
  type = list(object({
    env_name       = string
    kv_secret_name = string
  }))
  default = []
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}

# ── Workload profile ──────────────────────────────────────────────────────────
variable "workload_profile_name" {
  description = <<-EOT
    Workload profile this resource runs on.

    The sandbox environment reports a single profile:
      [{ "name": "Consumption", "workloadProfileType": "Consumption" }]

    Azure assigns that profile itself. If Terraform does not declare it, every
    plan proposes `workload_profile_name = "Consumption" -> null`, i.e. stripping
    the assignment from a live app. Declaring it makes config match reality and
    the diff disappear.

    Deliberately NOT paired with a workload_profile block on
    azurerm_container_app_environment: azurerm#31840 makes that plan never
    converge (min/max counts round-trip as 0).
  EOT
  type    = string
  default = "Consumption"
}
