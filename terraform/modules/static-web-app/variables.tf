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
variable "cae_east_id" {
  description = "East CAE resource ID — used for Private Link origin"
  type        = string
}

variable "cae_west_id" {
  description = "West CAE resource ID — used for Private Link origin"
  type        = string
}

variable "cae_east_static_ip" {
  description = "East CAE internal load balancer IP"
  type        = string
}

variable "cae_west_static_ip" {
  description = "West CAE internal load balancer IP"
  type        = string
}

variable "cae_east_default_domain" {
  description = "East CAE default domain"
  type        = string
}

variable "cae_west_default_domain" {
  description = "West CAE default domain"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
