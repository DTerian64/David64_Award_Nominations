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

variable "log_analytics_workspace_primary_id" {
  description = "Primary Log Analytics workspace resource ID — Grafana data source"
  type        = string
}

variable "log_analytics_workspace_secondary_id" {
  description = "Secondary Log Analytics workspace resource ID — Grafana data source"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
