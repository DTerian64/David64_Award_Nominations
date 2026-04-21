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

variable "admin_user_object_ids" {
  description = "Object IDs of home-tenant (or B2B guest) users to assign the AWard_Nomination_Admin app role. External-tenant users must be assigned via their own tenant's portal."
  type        = list(string)
  default     = []
}

variable "admin_app_role_id" {
  description = "UUID of the AWard_Nomination_Admin app role. Set this to the existing UUID from the app manifest if the role was created manually — prevents Terraform from deleting and recreating it. Leave empty to let Terraform generate a new UUID."
  type        = string
  default     = ""
}
