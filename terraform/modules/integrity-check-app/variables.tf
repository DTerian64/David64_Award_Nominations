variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "environment" { type = string }
variable "app_name" { type = string }
variable "container_app_environment_id" { type = string }
variable "workload_profile_name" {
  type    = string
  default = "Consumption"
}
variable "acr_login_server" { type = string }
variable "acr_admin_username" {
  type      = string
  sensitive = true
}
variable "acr_admin_password" {
  type      = string
  sensitive = true
}
variable "service_bus_fqns" { type = string }
variable "service_bus_topic_name" { type = string }
variable "service_bus_topic_id" { type = string }
variable "service_bus_subscription_name" { type = string }
variable "key_vault_id" { type = string }
variable "key_vault_uri" { type = string }
variable "storage_account_id" { type = string }
variable "storage_account_name" { type = string }
variable "model_container_name" { type = string }
variable "openai_id" { type = string }
variable "openai_endpoint" { type = string }
variable "openai_deployment" { type = string }
variable "openai_api_version" { type = string }
variable "model_idle_ttl_seconds" {
  type    = number
  default = 600
}
variable "graph_snapshot_cache_size" {
  type    = number
  default = 8
}
variable "min_replicas" {
  type    = number
  default = 0
}
variable "max_replicas" {
  type    = number
  default = 2
}
variable "cpu" {
  type    = number
  default = 1.0
}
variable "memory" {
  type    = string
  default = "2Gi"
}
variable "tags" {
  type    = map(string)
  default = {}
}
