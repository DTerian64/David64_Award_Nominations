# modules/app-registrations/variables.tf

variable "environment" {
  description = "Environment name — appended to app registration display names"
  type        = string
}

variable "swa_urls" {
  description = "Static Web App redirect URIs for the SPA — added after SWA is created"
  type        = list(string)
  default     = []
}
