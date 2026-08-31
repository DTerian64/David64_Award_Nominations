# modules/log-analytics/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location_primary" {
  description = "Primary region — passed from environment, no default"
  type        = string
}

variable "location_secondary" {
  description = "Secondary region — passed from environment, no default"
  type        = string
}

variable "workspace_name_primary" {
  description = "Log Analytics workspace name for primary CAE"
  type        = string
}

variable "workspace_name_secondary" {
  description = "Log Analytics workspace name for secondary CAE"
  type        = string
}

variable "retention_in_days" {
  description = "Log retention in days — matches existing (30)"
  type        = number
  default     = 30

  validation {
    condition     = var.retention_in_days >= 30 && var.retention_in_days <= 730
    error_message = "Retention must be between 30 and 730 days."
  }
}

variable "daily_quota_gb" {
  description = "Daily ingestion cap (GB) for both workspaces — hard stop against runaway Log Analytics cost. -1 = unlimited (Azure default)."
  type        = number
  default     = -1

  validation {
    condition     = var.daily_quota_gb == -1 || var.daily_quota_gb > 0
    error_message = "daily_quota_gb must be -1 (unlimited) or a positive number."
  }
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
