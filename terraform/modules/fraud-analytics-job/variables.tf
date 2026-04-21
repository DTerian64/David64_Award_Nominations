# modules/fraud-analytics-job/variables.tf

variable "job_name" {
  description = "Container Apps Job name. Convention: award-fraud-analytics-{env}"
  type        = string
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  description = "Azure region — must match the Container Apps Environment."
  type        = string
}

variable "container_app_environment_id" {
  description = "Resource ID of the Container Apps Environment to host this job."
  type        = string
}

variable "environment" {
  description = "Deployment environment label (sandbox / dev / production)."
  type        = string
}

# ── Schedule ──────────────────────────────────────────────────────────────────
variable "cron_expression" {
  description = "Cron expression for the weekly run. Default: Monday 02:00 UTC."
  type        = string
  default     = "0 2 * * 1"
}

# ── Identity ──────────────────────────────────────────────────────────────────
variable "analytics_identity_id" {
  description = "Resource ID of the User-Assigned Managed Identity for this job."
  type        = string
}

variable "analytics_identity_client_id" {
  description = "Client ID of the User-Assigned Managed Identity (required by DefaultAzureCredential when multiple MIs are present)."
  type        = string
}

# ── ACR ───────────────────────────────────────────────────────────────────────
variable "acr_login_server" {
  type = string
}

variable "acr_admin_username" {
  type = string
}

variable "acr_admin_password" {
  type      = string
  sensitive = true
}

# ── Key Vault ─────────────────────────────────────────────────────────────────
variable "key_vault_uri" {
  description = "Base URI of the Key Vault (e.g. https://kv-award-sandbox.vault.azure.net/)."
  type        = string
}

variable "kv_secret_references" {
  description = "List of Key Vault secrets to surface as environment variables. Each entry: { env_name, kv_secret_name }."
  type = list(object({
    env_name       = string
    kv_secret_name = string
  }))
  default = []
}

# ── Storage ───────────────────────────────────────────────────────────────────
variable "storage_account_name" {
  description = "Storage account where trained .pkl model files are persisted."
  type        = string
}

variable "model_container_name" {
  description = "Blob container name for ML model artefacts."
  type        = string
  default     = "ml-models"
}

# ── Compute ───────────────────────────────────────────────────────────────────
variable "cpu" {
  description = "vCPU allocation per replica. 2 vCPU supports scikit-learn + networkx peak usage."
  type        = number
  default     = 2
}

variable "memory" {
  description = "Memory allocation per replica."
  type        = string
  default     = "4Gi"
}

# ── Optional caller-supplied env vars ─────────────────────────────────────────
variable "environment_variables" {
  description = "Additional non-secret environment variables to inject."
  type = list(object({
    name  = string
    value = string
  }))
  default = []
}

# ── Tags ──────────────────────────────────────────────────────────────────────
variable "tags" {
  type    = map(string)
  default = {}
}
