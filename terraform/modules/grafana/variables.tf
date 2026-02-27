# modules/grafana/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — matches existing Grafana (westus2)"
  type        = string
  default     = "westus2"
}

variable "grafana_name" {
  description = "Grafana workspace name — globally unique"
  type        = string
  default     = "awardnomination-grafana"
}

variable "log_analytics_workspace_east_id" {
  description = "East Log Analytics workspace resource ID — Grafana data source"
  type        = string
}

variable "log_analytics_workspace_west_id" {
  description = "West Log Analytics workspace resource ID — Grafana data source"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
