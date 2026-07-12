# modules/sql-access/variables.tf

variable "environment" {
  description = "Environment name (e.g. sandbox, prod). Groups are environment-scoped."
  type        = string
}

variable "runtime_identity_principal_ids" {
  description = "Map of runtime workload name => Managed Identity principal (object) ID -> read/write group."
  type        = map(string)
}

variable "readwrite_group_name" {
  description = "Override the read/write group display name. Empty => sql-app-readwrite-<environment>."
  type        = string
  default     = ""
}

variable "migrations_group_name" {
  description = "Override the migrations group display name. Empty => sql-migrations-<environment>."
  type        = string
  default     = ""
}

variable "admins_group_name" {
  description = "Override the admins group display name. Empty => sql-admins-<environment>."
  type        = string
  default     = ""
}
