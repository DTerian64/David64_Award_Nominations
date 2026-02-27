# modules/networking/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "environment" {
  description = "Environment name — appended to resource names (prod, dev)"
  type        = string

  validation {
    condition     = contains(["prod", "dev"], var.environment)
    error_message = "Environment must be prod or dev."
  }
}

variable "location_east" {
  description = "Primary region"
  type        = string
  default     = "eastus"
}

variable "location_west" {
  description = "Secondary region for ACA geo-redundancy"
  type        = string
  default     = "westus"
}

variable "vnet_east_address_space" {
  description = "Address space for East US VNet"
  type        = string

  validation {
    condition     = can(cidrhost(var.vnet_east_address_space, 0))
    error_message = "Must be a valid CIDR block e.g. 10.0.0.0/16"
  }
}

variable "vnet_west_address_space" {
  description = "Address space for West US VNet — must not overlap with East"
  type        = string

  validation {
    condition     = can(cidrhost(var.vnet_west_address_space, 0))
    error_message = "Must be a valid CIDR block e.g. 10.1.0.0/16"
  }
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
