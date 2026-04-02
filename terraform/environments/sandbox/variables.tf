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
  default = "DEBUG"
}
variable "blob_sas_expiry_hours" {
  type    = number
  default = 24
}
variable "email_action_token_expiry_hours" {
  type    = number
  default = 24
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
