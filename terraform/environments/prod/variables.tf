# environments/prod/variables.tf

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

# Log Analytics
variable "workspace_name_primary"   { type = string }
variable "workspace_name_secondary" { type = string }

# Container Apps
variable "cae_name_primary"    { type = string }
variable "cae_name_secondary"  { type = string }
variable "app_name_primary"    { type = string }
variable "app_name_secondary"  { type = string }
variable "min_replicas"        { type = number }
variable "max_replicas"        { type = number }

# Static ACA config
variable "location_primary" {
  type    = string
  default = "eastus"
}
variable "location_secondary" {
  type    = string
  default = "westus"
}
variable "model_blob_name"      { type = string }
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
  default = 24
}

# Front Door
variable "afd_profile_name"     { type = string }
variable "afd_endpoint_name"    { type = string }

# Static Web App
variable "swa_name"             { type = string }
# Note: Azure AD values are read from existing app registrations via data sources

# Grafana
variable "grafana_name"         { type = string }

# Key Vault secrets
variable "secrets" {
  type      = map(string)
  sensitive = true
}

# ── Service Bus ───────────────────────────────────────────────────────────────
variable "service_bus_namespace_name" {
  description = "Service Bus namespace name — globally unique. Convention: sb-award-prod"
  type        = string
}

# ── Auxiliary Container App ───────────────────────────────────────────────────
variable "auxiliary_container_app_name" {
  description = "Auxiliary Container App name — must be unique within the CAE. Convention: award-auxiliary-prod"
  type        = string
}
