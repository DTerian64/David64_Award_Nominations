# modules/static-web-app/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "westus2"
}

variable "app_name" {
  description = "Static Web App name"
  type        = string
}

variable "afd_hostname" {
  description = "AFD public hostname — used as VITE_API_URL base"
  type        = string
}

# ── Azure AD / MSAL settings for React frontend ───────────────────────────────
variable "vite_api_url" {
  description = "Backend API URL — injected as VITE_API_URL"
  type        = string
}

variable "vite_api_client_id" {
  description = "Azure AD API app registration client ID — VITE_API_CLIENT_ID"
  type        = string
}

variable "vite_api_scope" {
  description = "Azure AD API scope — VITE_API_SCOPE e.g. api://CLIENT_ID/access_as_user"
  type        = string
}

variable "vite_client_id" {
  description = "Azure AD SPA app registration client ID — VITE_CLIENT_ID"
  type        = string
}

variable "vite_tenant_id" {
  description = "Azure AD tenant ID — VITE_TENANT_ID"
  type        = string
}

variable "custom_domain" {
  description = "Optional custom domain. Leave empty to skip."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
