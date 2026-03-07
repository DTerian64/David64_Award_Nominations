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

# ── From container-apps module outputs ────────────────────────────────────────
# Origins point directly to each Container App's public FQDN.
# e.g. award-api-eastus.ambitiousflower-6294c285.eastus.azurecontainerapps.io
variable "container_app_east_fqdn" {
  description = "East Container App public FQDN — used as AFD origin hostname"
  type        = string
}

variable "container_app_west_fqdn" {
  description = "West Container App public FQDN — used as AFD origin hostname"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
