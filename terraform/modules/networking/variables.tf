# modules/networking/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "environment" {
  description = "Environment name — appended to resource names (e.g. prod, sandbox, dev)"
  type        = string

  validation {
    condition     = length(var.environment) > 0
    error_message = "Environment must be a non-empty string."
  }
}

variable "location_primary" {
  description = "Primary region"
  type        = string
  default     = "eastus"
}

variable "location_secondary" {
  description = "Secondary region for ACA geo-redundancy"
  type        = string
  default     = "westus"
}

variable "vnet_primary_address_space" {
  description = "Address space for primary VNet"
  type        = string

  validation {
    condition     = can(cidrhost(var.vnet_primary_address_space, 0))
    error_message = "Must be a valid CIDR block e.g. 10.0.0.0/16"
  }
}

variable "vnet_secondary_address_space" {
  description = "Address space for secondary VNet — must not overlap with primary"
  type        = string

  validation {
    condition     = can(cidrhost(var.vnet_secondary_address_space, 0))
    error_message = "Must be a valid CIDR block e.g. 10.1.0.0/16"
  }
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
