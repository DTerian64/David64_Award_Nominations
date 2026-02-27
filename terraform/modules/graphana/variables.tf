# modules/static-web-app/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — matches existing SWA (westus2)"
  type        = string
  default     = "westus2"
}

variable "app_name" {
  description = "Static Web App name"
  type        = string
  default     = "award-nomination-frontend"
}

variable "afd_hostname" {
  description = "AFD public hostname — injected as REACT_APP_API_URL for the React build"
  type        = string
}

variable "custom_domain" {
  description = "Optional custom domain e.g. awards.yourdomain.com. Leave empty to skip."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
