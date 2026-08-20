# modules/fraud-analytics-job/variables.tf

variable "job_name" {
  description = "Container Apps Job name. Convention: award-fraud-analytics-{env}"
  type        = string
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  description = "Azure region — must match the Container Apps Environment."
  type        = string
}

variable "container_app_environment_id" {
  description = "Resource ID of the Container Apps Environment to host this job."
  type        = string
}

variable "environment" {
  description = "Deployment environment label (sandbox / dev / production)."
  type        = string
}

# ── Schedule ──────────────────────────────────────────────────────────────────
variable "cron_expression" {
  description = "Cron expression for the weekly run. Default: Monday 02:00 UTC."
  type        = string
  default     = "0 2 * * 1"
}

# ── Identity ──────────────────────────────────────────────────────────────────
variable "analytics_identity_id" {
  description = "Resource ID of the User-Assigned Managed Identity for this job."
  type        = string
}

variable "analytics_identity_client_id" {
  description = "Client ID of the User-Assigned Managed Identity (required by DefaultAzureCredential when multiple MIs are present)."
  type        = string
}

# ── ACR ───────────────────────────────────────────────────────────────────────
variable "acr_login_server" {
  type = string
}

variable "acr_admin_username" {
  type = string
}

variable "acr_admin_password" {
  type      = string
  sensitive = true
}

# ── Key Vault ─────────────────────────────────────────────────────────────────
variable "key_vault_uri" {
  description = "Base URI of the Key Vault (e.g. https://kv-award-sandbox.vault.azure.net/)."
  type        = string
}

variable "kv_secret_references" {
  description = "List of Key Vault secrets to surface as environment variables. Each entry: { env_name, kv_secret_name }."
  type = list(object({
    env_name       = string
    kv_secret_name = string
  }))
  default = []
}

# ── Storage ───────────────────────────────────────────────────────────────────
variable "storage_account_name" {
  description = "Storage account where trained .pkl model files are persisted."
  type        = string
}

variable "model_container_name" {
  description = "Blob container name for ML model artefacts."
  type        = string
  default     = "ml-models"
}

# ── Workload profile ──────────────────────────────────────────────────────────
variable "workload_profile_name" {
  description = <<-EOT
    Workload profile the job runs on.

    Confirmed 2026-08-20 that the environment reports a single profile:
      [{ "name": "Consumption", "workloadProfileType": "Consumption" }]

    That matters for sizing, not just tidiness. A *Consumption only* environment
    (one with no workload profiles at all) caps every app at 2 vCPU / 4Gi, which
    would have made the 4 / 8Gi allocation below invalid. Because a Consumption
    workload profile is present, the full 0.25-4 vCPU / 0.5-8Gi range is
    available, and 4 / 8Gi is the ceiling of that range.

    Declaring it here stops the provider proposing this value on every plan.
    workload_profile_name is Optional and NOT ForceNew on
    azurerm_container_app_job in azurerm 3.117, so this is an in-place update.

    Deliberately NOT paired with a workload_profile block on
    azurerm_container_app_environment. Azure adds the default Consumption profile
    itself, and declaring it in Terraform triggers a known provider bug
    (hashicorp/terraform-provider-azurerm#31840) where min/max counts round-trip
    as 0 and the plan never converges. The only fix offered there is
    ignore_changes on the environment, which is worse than leaving it undeclared.

    Set this to a Dedicated profile name if the GNN stage ever outgrows 4 / 8Gi -
    that is the only route above the Consumption ceiling.
  EOT
  type    = string
  default = "Consumption"
}

# ── Compute ───────────────────────────────────────────────────────────────────
variable "cpu" {
  description = <<-EOT
    vCPU allocation per replica.

    Was 2, which covered scikit-learn + networkx. Raised to 4 for the GNN
    training stage (ADR-0002): PyTorch Geometric message passing over the
    heterogeneous graph is the job's new peak, and 300 epochs on 2 vCPU push
    that stage into dominating the weekly run.

    NOTE: 4 vCPU / 8Gi is the ceiling for the ACA Consumption workload profile.
    There is no headroom above this without moving to a Dedicated profile, so
    if the GNN stage outgrows it the fix is a profile change, not a bigger number.
  EOT
  type        = number
  default     = 4
}

variable "memory" {
  description = <<-EOT
    Memory allocation per replica. Raised from 4Gi to 8Gi for the GNN stage:
    the hetero graph, node feature matrices and the autograd tape are held in
    memory at the same time, on top of the existing RF feature frames.

    ACA pairs memory with cpu at 2Gi per vCPU, so this must move with var.cpu.
  EOT
  type        = string
  default     = "8Gi"
}

# ── Optional caller-supplied env vars ─────────────────────────────────────────
variable "environment_variables" {
  description = "Additional non-secret environment variables to inject."
  type = list(object({
    name  = string
    value = string
  }))
  default = []
}

# ── Tags ──────────────────────────────────────────────────────────────────────
variable "tags" {
  type    = map(string)
  default = {}
}
