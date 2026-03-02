# modules/sql/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region for SQL Server"
  type        = string
  default     = "westus2"
}

variable "server_name" {
  description = "SQL Server name — must be globally unique"
  type        = string
}

variable "database_name" {
  description = "SQL Database name"
  type        = string
}

variable "admin_login" {
  description = "SQL Server administrator login"
  type        = string
  sensitive   = true
}

variable "admin_password" {
  description = "SQL Server administrator password"
  type        = string
  sensitive   = true
}

variable "private_endpoint_subnet_id" {
  description = "Subnet ID for the private endpoint (subnet-privatelinks)"
  type        = string
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for privatelink.database.windows.net"
  type        = string
}

variable "allowed_ips" {
  description = "List of local IPs to whitelist on the SQL firewall"
  type        = list(string)
  default     = []
}

variable "public_network_access_enabled" {
  description = "Allow public network access. Set false after private endpoint confirmed."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}

variable "location_east" {
  description = "East US region — used for private endpoint (must match subnet region)"
  type        = string
  default     = "eastus"
}
