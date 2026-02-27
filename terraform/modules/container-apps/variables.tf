# modules/log-analytics/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location_east" {
  description = "East US region for the East workspace"
  type        = string
  default     = "eastus"
}

variable "location_west" {
  description = "West US region for the West workspace"
  type        = string
  default     = "westus"
}

variable "workspace_name_east" {
  description = "Log Analytics workspace name for East US CAE"
  type        = string
}

variable "workspace_name_west" {
  description = "Log Analytics workspace name for West US CAE"
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

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
