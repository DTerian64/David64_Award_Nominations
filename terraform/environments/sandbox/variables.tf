# environments/dev/variables.tf

variable "resource_group_name"  { type = string }
variable "environment"          { type = string }
variable "my_ips"               { type = list(string) }

# SQL
variable "sql_server_name"      { type = string }
variable "sql_database_name"    { type = string }
variable "sql_admin_login" {
  type      = string
  sensitive = true
}
variable "sql_admin_password" {
  type      = string
  sensitive = true
}

# ACR
variable "acr_name"             { type = string }

# Storage
variable "storage_account_name" { type = string }

# Key Vault
variable "key_vault_name"       { type = string }

# OpenAI
variable "openai_name"          { type = string }
variable "openai_api_version"   { type = string }
variable "model_capacity_tpm"   { type = number }

# App config
variable "api_base_url"         { type = string }
variable "logging_level" {
  type    = string
  default = "INFO"
}
variable "blob_sas_expiry_hours" {
  type    = number
  default = 24
}
variable "email_action_token_expiry_hours" {
  type    = number
  default = 72
}
variable "model_idle_ttl_seconds" {
  description = "Seconds a per-tenant fraud model can sit idle before being evicted from memory. Shorter in sandbox to make eviction observable during development."
  type        = number
  default     = 600
}
variable "model_eviction_interval_seconds" {
  description = "How often the eviction background loop runs. Faster in sandbox for easier testing."
  type        = number
  default     = 120
}
variable "email_action_secret_key" {
  description = "Secret key used to sign and verify email action tokens"
  type        = string
  sensitive   = true
}

# Front Door
variable "afd_profile_name"     { type = string }
variable "afd_endpoint_name"    { type = string }

# Static Web App
variable "swa_name"             { type = string }

variable "swa_custom_domains" {
  description = "Custom domains to bind to the SWA. Each entry creates a DNS CNAME record and an SWA domain binding. Free SKU supports 2; Standard SKU supports 5."
  type        = list(string)
  default     = []
}

variable "dns_zone_resource_group" {
  description = "Resource group containing the terianix.ai Azure DNS zone."
  type        = string
  default     = "rg_platform"
}

# Azure AD — SWA redirect URIs added after first apply
variable "swa_redirect_urls" {
  type    = list(string)
  default = []
}

# Azure AD — admin role assignments
variable "admin_user_object_ids" {
  description = "Object IDs of home-tenant or B2B-guest users to receive AWard_Nomination_Admin"
  type        = list(string)
  default     = []
}

variable "admin_app_role_id" {
  description = "UUID of the existing AWard_Nomination_Admin app role (from app manifest). Set this before first apply to prevent Terraform from recreating the role."
  type        = string
  default     = ""
}

# CORS — populated by mid-terraform.ps1 after first apply
variable "cors_allowed_origins" {
  description = "Comma-separated CORS allowed origins injected into container app env vars"
  type        = string
  default     = ""
}

# Log Analytics
variable "workspace_name_primary" {
  description = "Log Analytics workspace name — Primary region"
  type        = string
}

variable "workspace_name_secondary" {
  description = "Log Analytics workspace name — Secondary region"
  type        = string
}

# Container Apps
variable "cae_name_primary"   { type = string }
variable "cae_name_secondary" { type = string }
variable "app_name_primary"   { type = string }
variable "app_name_secondary" { type = string }
variable "min_replicas" {
  type    = number
  default = 0
}
variable "max_replicas" {
  type    = number
  default = 1
}

# Location
variable "location_primary" {
  type    = string
  default = "eastus2"
}

variable "location_secondary" {
  type    = string
  default = "westus2"
}

variable "sql_location" {
  description = "Azure region for SQL Server — subscription restricts SQL in eastus/eastus2. westus2 is confirmed available."
  type        = string
  default     = "westus2"
}

# Secrets
variable "secrets" {
  type      = map(string)
  sensitive = true
}

variable "model_blob_name" {
  type    = string
  default = "fraud_detection_model.pkl"
}

# ── Service Bus ───────────────────────────────────────────────────────────────
variable "service_bus_namespace_name" {
  description = "Service Bus namespace name — globally unique. Convention: sb-award-{env}"
  type        = string
}

# ── Auxiliary Container App ───────────────────────────────────────────────────
variable "auxiliary_container_app_name" {
  description = "Auxiliary Container App name — must be unique within the CAE. Convention: award-auxiliary-{env}"
  type        = string
}

variable "integrity_check_container_app_name" {
  description = "Integrity Check Container App name. Convention: award-integrity-check-{env}"
  type        = string
  default     = "award-integrity-check-sandbox"
}

# ── Fraud Analytics Job ───────────────────────────────────────────────────────
variable "fraud_analytics_job_name" {
  description = "Container Apps Job name for the fraud analytics pipeline. Convention: award-fraud-analytics-{env}"
  type        = string
  default     = "award-fraud-analytics-sandbox"
}

variable "fraud_analytics_cron" {
  description = "Cron expression for the weekly fraud analytics run. Default: Monday 02:00 UTC."
  type        = string
  default     = "0 2 * * 1"
}

variable "fraud_analytics_ring_max_cluster_size" {
  description = <<-EOT
    Maximum SCC size to report as a Ring finding.
    SCCs larger than this value are suppressed — useful when synthetic or
    seeded data produces artificially dense graphs with very large clusters.
    Set to 0 for no upper limit (production default).
    Example: set to 4 to see only tight 3–4 node rings.
  EOT
  type    = number
  default = 4
}

variable "fraud_analytics_detection_window_days" {
  description = <<-EOT
    Rolling lookback window (in days) for graph pattern detection.
    Only nominations submitted within this window are loaded into the
    detector. Ring / ApproverAffinity patterns need a longer window than
    CopyPaste / TransactionalLanguage, so a single value is used and set
    to cover the longest-horizon pattern (rings: ~6 months).

    Set to a large value (e.g. 3650) on first deploy to process full
    history, then lower to 180 for steady-state weekly runs.
  EOT
  type        = number
  default     = 180
}

# ── Payroll Broker ────────────────────────────────────────────────────────────
variable "payroll_broker_container_app_name" {
  description = "Payroll Broker Container App name. Convention: award-payroll-broker-{env}"
  type        = string
  default     = "award-payroll-broker-sandbox"
}

variable "payroll_broker_custom_domain" {
  description = "Custom domain for the Payroll Broker routed via AFD. A CNAME record is created in the terianix.ai DNS zone pointing to the AFD endpoint. Must match the domain registered in Gusto's developer portal as the redirect/webhook base URL."
  type        = string
  default     = "payroll-broker.terianix.ai"
}

# Gusto OAuth credentials — stored in Key Vault; never appear in tfvars in plaintext.
# Set via environment variable or a secrets.auto.tfvars (gitignored).
variable "gusto_client_id" {
  description = "Gusto OAuth application client ID — obtained from dev.gusto.com after registering the app. Not sensitive in Gusto's model but stored in KV for consistency."
  type        = string
  sensitive   = true
}

variable "gusto_client_secret" {
  description = "Gusto OAuth application client secret — obtained from dev.gusto.com. Stored in Key Vault as GUSTO-CLIENT-SECRET."
  type        = string
  sensitive   = true
}

variable "gusto_webhook_secret" {
  description = "Shared secret used to validate X-Gusto-Signature on inbound Gusto webhook callbacks. Set this in the Gusto developer portal webhook config AND here. Generate once: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
  type        = string
  sensitive   = true
}

variable "rippling_client_id" {
  description = "Rippling OAuth application client ID — obtained from the Rippling developer portal after App Shop approval."
  type        = string
  sensitive   = true
}

variable "rippling_client_secret" {
  description = "Rippling OAuth application client secret — obtained from the Rippling developer portal. Stored in Key Vault as RIPPLING-CLIENT-SECRET."
  type        = string
  sensitive   = true
}

variable "rippling_webhook_secret" {
  description = "Shared secret used to validate X-Rippling-Signature on inbound Rippling webhook callbacks. Set in the Rippling developer portal AND here. Generate once: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
  type        = string
  sensitive   = true
}

# ── Workday Proxy ─────────────────────────────────────────────────────────────
variable "workday_webhook_secret" {
  description = "Shared secret sent as X-Api-Key by Workday_Proxy when calling the Award API webhook. Must match WORKDAY_WEBHOOK_SECRET on the Workday_Proxy container. Generate once: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
  type        = string
  sensitive   = true
}

# ── Fraud Analytics Job callback ───────────────────────────────────────────────
variable "fraud_analytics_job_webhook_secret" {
  description = "Shared secret sent as X-Internal-Key by the fraud-analytics-job when calling /api/internal/refresh-fraud-model after uploading new model pkls. Must match JOB_CALLBACK_SECRET on both the API container apps and the fraud-analytics-job. Generate once: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
  type        = string
  sensitive   = true
}

# ── HRBP SLA Logic App ────────────────────────────────────────────────────────
variable "hrbp_sla_webhook_secret" {
  description = "Shared secret sent as X-Internal-Key by la-award-hrbp-sla when calling /api/internal/checkPendingHRBPReview. Must match HRBP_SLA_WEBHOOK_SECRET on the backend container apps. Generate once: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
  type        = string
  sensitive   = true
}

variable "hrbp_sla_hours" {
  description = "Hours a nomination can sit in PendingHRBPReview before it is considered SLA-breached and escalation emails are sent. Default: 72 hours (3 business days)."
  type        = number
  default     = 72
}

# ── Demo tenant — self-registration (graph_admin.py / demo_router.py) ─────────
variable "demo_aad_tenant_id" {
  description = "Azure AD tenant GUID for the Demo Terian Services tenant (DEMO_AAD_TENANT_ID). Non-sensitive — it appears in token tid claims."
  type        = string
  default     = ""
}

variable "demo_graph_client_id" {
  description = "Client ID of the Award Nomination Seeder app registration in the Demo tenant (DEMO_GRAPH_CLIENT_ID). Non-sensitive."
  type        = string
  default     = ""
}

variable "demo_graph_client_secret" {
  description = "Client secret for the Award Nomination Seeder app in the Demo tenant (DEMO_GRAPH_CLIENT_SECRET). Stored in Key Vault as DEMO-GRAPH-CLIENT-SECRET."
  type        = string
  sensitive   = true
  default     = ""
}

variable "demo_allowed_emails" {
  description = "Comma-separated personal email addresses that bypass the work-email domain block on the demo registration form (owner/developer test accounts). Passed to backend as DEMO_ALLOWED_EMAILS and to the SWA as VITE_DEMO_ALLOWED_EMAILS."
  type        = string
  default     = ""
}

variable "payroll_token_encryption_key" {
  description = "Base64-encoded 32-byte AES-256 key used to encrypt Gusto OAuth tokens stored in dbo.payroll_tokens. Generate once: python -c \"import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())\""
  type        = string
  sensitive   = true
}

variable "corporate_support_email" {
  description = "Fallback email address for payroll failure alerts when no Support-role users are configured for a tenant in dbo.UserRoles. Passed to the auxiliary service as CORPORATE_SUPPORT_EMAIL."
  type        = string
  default     = "support@terian-services.com"
}

# ── terianix.ai domain migration ──────────────────────────────────────────────

variable "swa_terianix_domains" {
  description = "New custom domains under terianix.ai to bind to the SWA. A CNAME record is created in the terianix.ai DNS zone for each entry, pointing to the SWA default hostname."
  type        = list(string)
  default     = []
}

variable "dns_zone_terianix_resource_group" {
  description = "Resource group containing the terianix.ai Azure DNS zone."
  type        = string
  default     = "rg_platform"
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone:DNS:Edit permission for terianix.ai. terianix.ai is delegated to Cloudflare (not Azure DNS), so all public DNS records must be managed here."
  type        = string
  sensitive   = true
}

variable "legacy_redirect_domains" {
  description = "Map of old terianix.ai hostname → new terianix.ai hostname. CNAME records for old hosts are updated to point to the AFD endpoint; AFD Rules Engine issues a 301 redirect to the mapped new hostname."
  type        = map(string)
  default     = {}
  # Example:
  # {
  #   "sandbox-awards.terianix.ai" = "sandbox-awards.terianix.ai"
  #   "acme-awards.terianix.ai"    = "acme-awards.terianix.ai"
  #   "demo-awards.terianix.ai"    = "demo-awards.terianix.ai"
  # }
}
