# modules/openai/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — OpenAI model availability varies by region"
  type        = string
  default     = "eastus"
}

variable "openai_name" {
  description = "Azure OpenAI account name — globally unique"
  type        = string
}

variable "model_deployment_name" {
  description = "Name of the model deployment — used as the model string in API calls"
  type        = string
  default     = "gpt-4.1"
}

variable "model_name" {
  description = "OpenAI model name"
  type        = string
  default     = "gpt-4.1"
}

variable "model_version" {
  description = "Model version — check Azure OpenAI docs for latest"
  type        = string
  default     = "2025-04-14"
}

variable "model_capacity_tpm" {
  description = "Tokens per minute capacity in thousands. 150 = 150K TPM (matches existing)"
  type        = number
  default     = 150

  validation {
    condition     = var.model_capacity_tpm >= 1 && var.model_capacity_tpm <= 450
    error_message = "Capacity must be between 1 and 450 (thousands of TPM)."
  }
}

variable "public_network_access_enabled" {
  description = "Allow public network access. Set false after private endpoint confirmed."
  type        = bool
  default     = true
}

variable "allowed_ips" {
  description = "Local IPs to whitelist for debugging"
  type        = list(string)
  default     = []
}

variable "private_endpoint_subnet_id" {
  description = "Subnet ID for the private endpoint (subnet-privatelinks)"
  type        = string
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for privatelink.openai.azure.com"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
