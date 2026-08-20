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
