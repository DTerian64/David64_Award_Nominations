# modules/key-vault/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — matches existing KV (eastus)"
  type        = string
  default     = "eastus"
}

variable "key_vault_name" {
  description = "Key Vault name — globally unique, 3-24 chars, alphanumeric and hyphens"
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9-]{3,24}$", var.key_vault_name))
    error_message = "Key Vault name must be 3-24 alphanumeric characters or hyphens."
  }
}

variable "public_network_access_enabled" {
  description = "Allow public network access. Set false after private endpoint confirmed."
  type        = bool
  default     = true
}

variable "allowed_ips" {
  description = "Local IPs to whitelist on KV firewall for debugging"
  type        = list(string)
  default     = []
}

variable "aca_subnet_ids" {
  description = "ACA subnet IDs for direct KV access via service endpoint"
  type        = list(string)
  default     = []
}

variable "aca_principal_ids" {
  description = "Object IDs of Container App managed identities that need secret read access"
  type        = list(string)
  default     = []
}

variable "private_endpoint_subnet_id" {
  description = "Subnet ID for the private endpoint (subnet-privatelinks)"
  type        = string
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for privatelink.vaultcore.azure.net"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
