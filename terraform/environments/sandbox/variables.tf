# environments/dev/variables.tf

variable "resource_group_name"  { type = string }
variable "environment"          { type = string }
variable "my_ips"               { type = list(string) }

# SQL
variable "sql_server_name"      { type = string }
variable "sql_database_name"    { type = string }
variable "sql_admin_login" {
  type      = string
  sensitive = true
}
variable "sql_admin_password" {
  type      = string
  sensitive = true
}

# ACR
variable "acr_name"             { type = string }

# Storage
variable "storage_account_name" { type = string }

# Key Vault
variable "key_vault_name"       { type = string }

# OpenAI
variable "openai_name"          { type = string }
variable "openai_api_version"   { type = string }
variable "model_capacity_tpm"   { type = number }

# App config
variable "api_base_url"         { type = string }
variable "logging_level" {
  type    = string
  default = "INFO"
}
variable "blob_sas_expiry_hours" {
  type    = number
  default = 24
}
variable "email_action_token_expiry_hours" {
  type    = number
  default = 72
}
variable "email_action_secret_key" {
  description = "Secret key used to sign and verify email action tokens"
  type        = string
  sensitive   = true
}

# Front Door
variable "afd_profile_name"     { type = string }
variable "afd_endpoint_name"    { type = string }

# Static Web App
variable "swa_name"             { type = string }

variable "swa_custom_domain" {
  description = "Optional custom domain for the SWA (e.g. dev-awards.terian-services.com). Leave empty to skip."
  type        = string
  default     = ""
}

variable "dns_zone_resource_group" {
  description = "Resource group containing the terian-services.com Azure DNS zone."
  type        = string
  default     = "rg_platform"
}

# Azure AD — SWA redirect URIs added after first apply
variable "swa_redirect_urls" {
  type    = list(string)
  default = []
}

# Azure AD — admin role assignments
variable "admin_user_object_ids" {
  description = "Object IDs of home-tenant or B2B-guest users to receive AWard_Nomination_Admin"
  type        = list(string)
  default     = []
}

variable "admin_app_role_id" {
  description = "UUID of the existing AWard_Nomination_Admin app role (from app manifest). Set this before first apply to prevent Terraform from recreating the role."
  type        = string
  default     = ""
}

# CORS — populated by mid-terraform.ps1 after first apply
variable "cors_allowed_origins" {
  description = "Comma-separated CORS allowed origins injected into container app env vars"
  type        = string
  default     = ""
}

# Log Analytics
variable "workspace_name_primary" {
  description = "Log Analytics workspace name — Primary region"
  type        = string
}

variable "workspace_name_secondary" {
  description = "Log Analytics workspace name — Secondary region"
  type        = string
}

# Container Apps
variable "cae_name_primary"   { type = string }
variable "cae_name_secondary" { type = string }
variable "app_name_primary"   { type = string }
variable "app_name_secondary" { type = string }
variable "min_replicas" {
  type    = number
  default = 0
}
variable "max_replicas" {
  type    = number
  default = 1
}

# Location
variable "location_primary" {
  type    = string
  default = "eastus2"
}

variable "location_secondary" {
  type    = string
  default = "westus2"
}

variable "sql_location" {
  description = "Azure region for SQL Server — subscription restricts SQL in eastus/eastus2. westus2 is confirmed available."
  type        = string
  default     = "westus2"
}

# Secrets
variable "secrets" {
  type      = map(string)
  sensitive = true
}

variable "model_blob_name" {
  type    = string
  default = "fraud_detection_model.pkl"
}

# ── Service Bus ───────────────────────────────────────────────────────────────
variable "service_bus_namespace_name" {
  description = "Service Bus namespace name — globally unique. Convention: sb-award-{env}"
  type        = string
}

# ── Auxiliary Container App ───────────────────────────────────────────────────
variable "auxiliary_container_app_name" {
  description = "Auxiliary Container App name — must be unique within the CAE. Convention: award-auxiliary-{env}"
  type        = string
}

# ── Fraud Analytics Job ───────────────────────────────────────────────────────
variable "fraud_analytics_job_name" {
  description = "Container Apps Job name for the fraud analytics pipeline. Convention: award-fraud-analytics-{env}"
  type        = string
  default     = "award-fraud-analytics-sandbox"
}

variable "fraud_analytics_cron" {
  description = "Cron expression for the weekly fraud analytics run. Default: Monday 02:00 UTC."
  type        = string
  default     = "0 2 * * 1"
}

variable "fraud_analytics_ring_max_cluster_size" {
  description = <<-EOT
    Maximum SCC size to report as a Ring finding.
    SCCs larger than this value are suppressed — useful when synthetic or
    seeded data produces artificially dense graphs with very large clusters.
    Set to 0 for no upper limit (production default).
    Example: set to 4 to see only tight 3–4 node rings.
  EOT
  type    = number
  default = 4
}

variable "fraud_analytics_detection_window_days" {
  description = <<-EOT
    Rolling lookback window (in days) for graph pattern detection.
    Only nominations submitted within this window are loaded into the
    detector. Ring / ApproverAffinity patterns need a longer window than
    CopyPaste / TransactionalLanguage, so a single value is used and set
    to cover the longest-horizon pattern (rings: ~6 months).

    Set to a large value (e.g. 3650) on first deploy to process full
    history, then lower to 180 for steady-state weekly runs.
  EOT
  type        = number
  default     = 180
}

# ── Workday Proxy ─────────────────────────────────────────────────────────────
variable "workday_webhook_secret" {
  description = "Shared secret sent as X-Api-Key by Workday_Proxy when calling the Award API webhook. Must match WORKDAY_WEBHOOK_SECRET on the Workday_Proxy container. Generate once: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
  type        = string
  sensitive   = true
}
