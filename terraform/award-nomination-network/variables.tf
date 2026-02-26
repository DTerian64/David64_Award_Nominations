# variables.tf

variable "resource_group_name" {
  description = "Existing resource group for all Award Nomination resources"
  type        = string
  default     = "rg_award_nomination"
}

variable "location_east" {
  description = "Primary region — East US"
  type        = string
  default     = "eastus"
}

variable "location_west" {
  description = "Secondary region — West US (for ACA geo-redundancy)"
  type        = string
  default     = "westus"
}

variable "vnet_east_address_space" {
  description = "Address space for East US VNet"
  type        = string
  default     = "10.0.0.0/16"
}

variable "vnet_west_address_space" {
  description = "Address space for West US VNet"
  type        = string
  default     = "10.1.0.0/16"
}

variable "my_ips" {
  description = "Your local public IP for SQL/Storage/KV firewall whitelist. Find it at whatismyip.com"
  type        = list(string)
  # Set this in terraform.tfvars — do not hardcode here
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default = {
    project     = "award-nomination"
    environment = "production"
    managed_by  = "terraform"
  }
}
