# modules/workday-proxy/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "app_name" {
  description = "Container App name. Convention: workday-proxy-{env}"
  type        = string
}

variable "environment" {
  description = "Deployment environment label (sandbox, dev, prod)"
  type        = string
}

variable "container_app_environment_id" {
  description = "ID of the Container App Environment to deploy into (primary CAE)"
  type        = string
}

# ── Managed identity ──────────────────────────────────────────────────────────

variable "identity_id" {
  description = "Resource ID of the pre-created User-Assigned Managed Identity"
  type        = string
}

variable "identity_client_id" {
  description = "Client ID of the pre-created User-Assigned Managed Identity"
  type        = string
}

# ── ACR ───────────────────────────────────────────────────────────────────────

variable "acr_login_server" {
  description = "Container Registry login server hostname"
  type        = string
}

variable "acr_admin_username" {
  description = "Container Registry admin username"
  type        = string
  sensitive   = true
}

variable "acr_admin_password" {
  description = "Container Registry admin password"
  type        = string
  sensitive   = true
}

# ── Downstream ────────────────────────────────────────────────────────────────

variable "award_api_base_url" {
  description = "Base URL of the Award Nomination API. Workday_Proxy calls {award_api_base_url}/api/webhooks/workday/payment-confirmed after simulated processing. In production, register this same webhook URL with real Workday."
  type        = string
  default     = ""
}

# ── Observability ─────────────────────────────────────────────────────────────

variable "appinsights_connection_string" {
  description = "Application Insights connection string"
  type        = string
  sensitive   = true
}

# ── Scale ─────────────────────────────────────────────────────────────────────

variable "min_replicas" {
  description = "Minimum replica count. Set to 0 in sandbox to scale to zero when idle."
  type        = number
  default     = 0
}

variable "max_replicas" {
  description = "Maximum replica count."
  type        = number
  default     = 2
}

variable "cpu" {
  description = "CPU allocation per replica"
  type        = number
  default     = 0.25
}

variable "memory" {
  description = "Memory allocation per replica"
  type        = string
  default     = "0.5Gi"
}

# ── Extra env vars ────────────────────────────────────────────────────────────

variable "environment_variables" {
  description = "Additional non-secret environment variables"
  type        = list(object({ name = string, value = string }))
  default     = []
}

# ── Tags ──────────────────────────────────────────────────────────────────────

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
