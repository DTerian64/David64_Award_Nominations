# environments/prod/variables.tf

variable "resource_group_name" { type = string }
variable "environment"         { type = string }
variable "my_ips"              { type = list(string) }

# SQL
variable "sql_server_name"     { type = string }
variable "sql_admin_login"     { type = string; sensitive = true }
variable "sql_admin_password"  { type = string; sensitive = true }

# ACR
variable "acr_name"            { type = string }

# Storage
variable "storage_account_name" { type = string }

# Key Vault
variable "key_vault_name"      { type = string }

# OpenAI
variable "openai_name"         { type = string }
variable "model_capacity_tpm"  { type = number }

# Log Analytics
variable "workspace_name_east" { type = string }
variable "workspace_name_west" { type = string }

# Container Apps
variable "cae_name_east"       { type = string }
variable "cae_name_west"       { type = string }
variable "app_name_east"       { type = string }
variable "app_name_west"       { type = string }
variable "min_replicas"        { type = number }
variable "max_replicas"        { type = number }

# Front Door
variable "afd_profile_name"    { type = string }
variable "afd_endpoint_name"   { type = string }

# Static Web App
variable "swa_name"            { type = string }

# Grafana
variable "grafana_name"        { type = string }
