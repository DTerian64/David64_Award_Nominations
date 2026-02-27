# modules/storage/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — matches existing storage (eastus)"
  type        = string
  default     = "eastus"
}

variable "storage_account_name" {
  description = "Storage account name — globally unique, 3-24 chars, lowercase alphanumeric only"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "Storage account name must be 3-24 lowercase alphanumeric characters."
  }
}

variable "allowed_ips" {
  description = "Local IPs to whitelist on storage firewall for debugging"
  type        = list(string)
  default     = []
}

variable "aca_subnet_ids" {
  description = "ACA subnet IDs that get direct storage access via service endpoint"
  type        = list(string)
  default     = []
}

variable "private_endpoint_subnet_id" {
  description = "Subnet ID for the private endpoint (subnet-privatelinks)"
  type        = string
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for privatelink.blob.core.windows.net"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
