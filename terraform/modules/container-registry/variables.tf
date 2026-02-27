# modules/container-registry/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — must match existing ACR region (westus2)"
  type        = string
  default     = "westus2"
}

variable "acr_name" {
  description = "ACR name — must be globally unique, alphanumeric only"
  type        = string
}

variable "sku" {
  description = "ACR SKU: Basic, Standard, or Premium"
  type        = string
  default     = "Basic"

  validation {
    condition     = contains(["Basic", "Standard", "Premium"], var.sku)
    error_message = "SKU must be Basic, Standard, or Premium."
  }
}

variable "admin_enabled" {
  description = "Enable admin user — required for ACA image pulls without managed identity"
  type        = bool
  default     = true
}

variable "public_network_access_enabled" {
  description = "Allow public network access. Set false after private endpoint confirmed."
  type        = bool
  default     = true
}

variable "private_endpoint_subnet_id" {
  description = "Subnet ID for the private endpoint (subnet-privatelinks)"
  type        = string
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for privatelink.azurecr.io"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
