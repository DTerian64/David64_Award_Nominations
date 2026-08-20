# modules/storage/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region — must be passed explicitly; no default to avoid silent region mismatch"
  type        = string
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

# ── Artifact recovery (ADR-0002) ──────────────────────────────────────────────
# Defaults are deliberately safe, so no environment has to opt in to being
# recoverable. Override per environment in terraform.tfvars if prod wants longer
# retention than sandbox.

variable "blob_versioning_enabled" {
  description = <<-EOT
    Keep prior versions of overwritten blobs. Required for ML model rollback:
    both pipelines write with overwrite=True, so without this an overwrite is
    destructive and a bad model cannot be reverted. Disabling this also disables
    the version-expiry management policy, since it has nothing to act on.
  EOT
  type        = bool
  default     = true
}

variable "blob_soft_delete_retention_days" {
  description = "Days a deleted blob stays recoverable. Azure permits 1-365."
  type        = number
  default     = 30

  validation {
    condition     = var.blob_soft_delete_retention_days >= 1 && var.blob_soft_delete_retention_days <= 365
    error_message = "Blob soft-delete retention must be between 1 and 365 days."
  }
}

variable "container_soft_delete_retention_days" {
  description = "Days a deleted container stays recoverable. Azure permits 1-365."
  type        = number
  default     = 30

  validation {
    condition     = var.container_soft_delete_retention_days >= 1 && var.container_soft_delete_retention_days <= 365
    error_message = "Container soft-delete retention must be between 1 and 365 days."
  }
}

variable "model_version_retention_days" {
  description = <<-EOT
    Days to keep superseded versions of ML model artifacts in ml-models.
    Must outlive the interval over which a model regression would be noticed —
    the weekly job means several retrains, not several days. 90 covers roughly a
    quarter of weekly runs.
  EOT
  type        = number
  default     = 90
}

variable "general_version_retention_days" {
  description = <<-EOT
    Days to keep superseded versions in the write-once containers (certificates,
    certificate-templates, extracts, tfstate). Longer than the model window
    because these change rarely and carry audit value.
  EOT
  type        = number
  default     = 365
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
