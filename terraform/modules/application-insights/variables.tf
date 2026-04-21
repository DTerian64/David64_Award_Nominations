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

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
