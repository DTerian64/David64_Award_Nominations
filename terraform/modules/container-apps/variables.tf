# modules/container-apps/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location_east" {
  description = "East US region"
  type        = string
  default     = "eastus"
}

variable "location_west" {
  description = "West US region"
  type        = string
  default     = "westus"
}

# ── CAE names ─────────────────────────────────────────────────────────────────
variable "cae_name_east" {
  description = "Container App Environment name — East US"
  type        = string
  default     = "cae-award-eastus"
}

variable "cae_name_west" {
  description = "Container App Environment name — West US"
  type        = string
  default     = "cae-award-westus"
}

# ── Container App names ───────────────────────────────────────────────────────
variable "app_name_east" {
  description = "Container App name — East US"
  type        = string
  default     = "award-api-eastus"
}

variable "app_name_west" {
  description = "Container App name — West US"
  type        = string
  default     = "award-api-westus"
}

# ── Networking — from networking module outputs ───────────────────────────────
variable "subnet_aca_east_id" {
  description = "East ACA subnet ID (delegated to Microsoft.App/environments)"
  type        = string
}

variable "subnet_aca_west_id" {
  description = "West ACA subnet ID (delegated to Microsoft.App/environments)"
  type        = string
}

# ── Log Analytics — from log-analytics module outputs ────────────────────────
variable "log_analytics_workspace_east_id" {
  description = "East Log Analytics workspace resource ID"
  type        = string
}

variable "log_analytics_workspace_west_id" {
  description = "West Log Analytics workspace resource ID"
  type        = string
}

# ── ACR — from container-registry module outputs ──────────────────────────────
variable "acr_login_server" {
  description = "ACR login server URL e.g. acrawardnomination.azurecr.io"
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

# ── Container sizing — matches existing (0.5 CPU, 1Gi) ───────────────────────
variable "cpu" {
  description = "CPU allocation per container"
  type        = number
  default     = 0.5
}

variable "memory" {
  description = "Memory allocation per container"
  type        = string
  default     = "1Gi"
}

variable "min_replicas" {
  description = "Minimum replicas — 0 allows scale to zero"
  type        = number
  default     = 1
}

variable "max_replicas" {
  description = "Maximum replicas"
  type        = number
  default     = 3
}

# ── Environment variables ─────────────────────────────────────────────────────
# Non-secret config values passed as plain environment variables.
# For secrets, use kv_secret_references instead — values never touch TF state.
variable "environment_variables" {
  description = "Non-secret environment variables to inject into Container Apps"
  type = list(object({
    name  = string
    value = string
  }))
  default = []
}

# ── Key Vault secret references ───────────────────────────────────────────────
# Three-tier chain: KV secret → ACA secret reference → env var
#   1. KV holds the value (fetched at runtime by managed identity — never in state)
#   2. ACA secret block references the KV secret URI
#   3. Env var in the container references the ACA secret name
#
# Convention: kv_secret_name uses UPPER-HYPHEN (e.g. "SQL-PASSWORD")
#             ACA secret name is derived as lower(kv_secret_name) (e.g. "sql-password")
#             env_name is what the app reads (e.g. "SQL_PASSWORD")
variable "kv_secret_references" {
  description = "Secrets to pull from Key Vault and expose as env vars via ACA secret references"
  type = list(object({
    env_name       = string  # env var name the app reads:  "SQL_PASSWORD"
    kv_secret_name = string  # Key Vault secret name:       "SQL-PASSWORD"
  }))
  default = []
}

variable "key_vault_uri" {
  description = "Key Vault URI (e.g. https://kv-award-prod.vault.azure.net/) — required when kv_secret_references is non-empty"
  type        = string
  default     = ""
}

# ── User-Assigned Managed Identities ─────────────────────────────────────────
# Created in the environment main.tf BEFORE the Container Apps, so KV access
# policies can be granted before Azure tries to resolve KV-backed secrets.
# Using user-assigned (not system-assigned) breaks the ordering race condition.
variable "aca_east_identity_id" {
  description = "Resource ID of the User-Assigned Managed Identity for the east Container App"
  type        = string
}

variable "aca_west_identity_id" {
  description = "Resource ID of the User-Assigned Managed Identity for the west Container App"
  type        = string
}

variable "internal_load_balancer_enabled" {
  description = "Set true to make the CAE internal-only (requires Front Door Premium + Private Link). Set false for public access via Front Door Standard."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
