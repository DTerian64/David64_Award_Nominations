# modules/front-door/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "afd_profile_name" {
  description = "AFD profile name"
  type        = string
  default     = "Award-Nomination-ADF"
}

variable "afd_endpoint_name" {
  description = "AFD endpoint name — becomes part of the public hostname"
  type        = string
  default     = "award-nomination-api"
}

# ── From container-apps module outputs ────────────────────────────────────────
# Origins point directly to each Container App's public FQDN.
# e.g. award-api-primary-sandbox.ambitiousflower-6294c285.westus2.azurecontainerapps.io
variable "container_app_primary_fqdn" {
  description = "Primary location Container App public FQDN — used as AFD origin hostname"
  type        = string
}

variable "container_app_secondary_fqdn" {
  description = "Secondary location Container App public FQDN — used as AFD origin hostname"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
