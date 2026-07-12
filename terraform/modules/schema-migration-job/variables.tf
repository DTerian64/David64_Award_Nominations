# modules/schema-migration-job/variables.tf
variable "job_name" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "environment" { type = string }
variable "container_app_environment_id" {
  description = "CAE id (VNet-integrated) -- job runs in-VNet, reaches private SQL via the private endpoint."
  type        = string
}

# User-assigned MI, member of sql-migrations-<env> (db_ddladmin). Auth to SQL is
# via this identity's Entra token -- no SQL_USER/PASSWORD.
variable "identity_id" { type = string }
variable "identity_client_id" { type = string }

# ACR image-pull credentials.
variable "acr_login_server" { type = string }
variable "acr_admin_username" { type = string }
variable "acr_admin_password" {
  type      = string
  sensitive = true
}

# Non-secret connection targets (env.py builds the ODBC string from these).
variable "sql_server_fqdn" { type = string }
variable "sql_database_name" { type = string }

variable "cpu" {
  type    = number
  default = 0.5
}
variable "memory" {
  type    = string
  default = "1Gi"
}
variable "replica_timeout_in_seconds" {
  type    = number
  default = 1800
}
variable "tags" {
  type    = map(string)
  default = {}
}
