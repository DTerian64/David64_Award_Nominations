# modules/application-insights/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — must match the Log Analytics workspace region"
  type        = string
}

variable "environment" {
  description = "Environment name (sandbox / dev / prod) — appended to resource names"
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Resource ID of the Log Analytics workspace to link both App Insights resources to"
  type        = string
}

variable "daily_data_cap_gb" {
  description = "Daily ingestion cap (GB) applied to both App Insights resources — cost safety net, independent of the workspace-level cap. Azure default is 100 GB if left at -1."
  type        = number
  default     = -1

  validation {
    condition     = var.daily_data_cap_gb == -1 || var.daily_data_cap_gb > 0
    error_message = "daily_data_cap_gb must be -1 (use Azure default of 100) or a positive number."
  }
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
