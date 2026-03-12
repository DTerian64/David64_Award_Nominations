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

# Azure AD — SWA redirect URIs added after first apply
variable "swa_redirect_urls" {
  type    = list(string)
  default = []
}

# CORS — populated by mid-terraform.ps1 after first apply
variable "cors_allowed_origins" {
  description = "Comma-separated CORS allowed origins injected into container app env vars"
  type        = string
  default     = ""
}

# Log Analytics
variable "workspace_name_east" {
  description = "Log Analytics workspace name — East US"
  type        = string
}

variable "workspace_name_west" {
  description = "Log Analytics workspace name — West US"
  type        = string
}

# Container Apps
variable "cae_name_east"  { type = string }
variable "cae_name_west"  { type = string }
variable "app_name_east"  { type = string }
variable "app_name_west"  { type = string }
variable "min_replicas" {
  type    = number
  default = 0
}
variable "max_replicas" {
  type    = number
  default = 1
}

# Location
variable "location_east" {
  type    = string
  default = "eastus"
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
