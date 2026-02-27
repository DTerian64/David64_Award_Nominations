# modules/container-apps/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location_east" {
  description = "East US region"
  type        = string
  default     = "eastus"
}

variable "location_west" {
  description = "West US region"
  type        = string
  default     = "westus"
}

# ── CAE names ─────────────────────────────────────────────────────────────────
variable "cae_name_east" {
  description = "Container App Environment name — East US"
  type        = string
  default     = "cae-award-eastus"
}

variable "cae_name_west" {
  description = "Container App Environment name — West US"
  type        = string
  default     = "cae-award-westus"
}

# ── Container App names ───────────────────────────────────────────────────────
variable "app_name_east" {
  description = "Container App name — East US"
  type        = string
  default     = "award-api-eastus"
}

variable "app_name_west" {
  description = "Container App name — West US"
  type        = string
  default     = "award-api-westus"
}

# ── Networking — from networking module outputs ───────────────────────────────
variable "subnet_aca_east_id" {
  description = "East ACA subnet ID (delegated to Microsoft.App/environments)"
  type        = string
}

variable "subnet_aca_west_id" {
  description = "West ACA subnet ID (delegated to Microsoft.App/environments)"
  type        = string
}

# ── Log Analytics — from log-analytics module outputs ────────────────────────
variable "log_analytics_workspace_east_id" {
  description = "East Log Analytics workspace resource ID"
  type        = string
}

variable "log_analytics_workspace_west_id" {
  description = "West Log Analytics workspace resource ID"
  type        = string
}

# ── ACR — from container-registry module outputs ──────────────────────────────
variable "acr_login_server" {
  description = "ACR login server URL e.g. acrawardnomination.azurecr.io"
  type        = string
}

variable "acr_admin_username" {
  description = "ACR admin username"
  type        = string
  sensitive   = true
}

variable "acr_admin_password" {
  description = "ACR admin password"
  type        = string
  sensitive   = true
}

# ── Container sizing — matches existing (0.5 CPU, 1Gi) ───────────────────────
variable "cpu" {
  description = "CPU allocation per container"
  type        = number
  default     = 0.5
}

variable "memory" {
  description = "Memory allocation per container"
  type        = string
  default     = "1Gi"
}

variable "min_replicas" {
  description = "Minimum replicas — 0 allows scale to zero"
  type        = number
  default     = 1
}

variable "max_replicas" {
  description = "Maximum replicas"
  type        = number
  default     = 3
}

# ── Environment variables ─────────────────────────────────────────────────────
# Passed in from environments/prod/main.tf using outputs from other modules
# Format: [{ name = "KEY", value = "VALUE" }, ...]
variable "environment_variables" {
  description = "Environment variables to inject into Container Apps"
  type = list(object({
    name  = string
    value = string
  }))
  default = []
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
