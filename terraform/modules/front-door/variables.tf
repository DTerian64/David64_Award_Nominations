# modules/front-door/variables.tf

variable "resource_group_name" {
  description = "Resource group to deploy into"
  type        = string
}

variable "afd_profile_name" {
  description = "AFD profile name"
  type        = string
  default     = "Award-Nomination-ADF"
}

variable "afd_endpoint_name" {
  description = "AFD endpoint name — becomes part of the public hostname"
  type        = string
  default     = "award-nomination-api"
}

# ── From container-apps module outputs ────────────────────────────────────────
# Origins point directly to each Container App's public FQDN.
# e.g. award-api-primary-sandbox.ambitiousflower-6294c285.westus2.azurecontainerapps.io
variable "container_app_primary_fqdn" {
  description = "Primary location Container App public FQDN — used as AFD origin hostname"
  type        = string
}

variable "container_app_secondary_fqdn" {
  description = "Secondary location Container App public FQDN — used as AFD origin hostname"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}

# ── Payroll Broker origin ─────────────────────────────────────────────────────
# Set to the payroll-broker Container App FQDN to enable the second AFD route.
# When non-empty, AFD creates a dedicated origin group + custom domain route for
# payroll-broker.terianix.ai, keeping payroll traffic fully separate from the
# nomination workflow route.
# Leave empty (default) to skip all payroll-broker AFD resources — useful when
# deploying to an environment that has not yet provisioned the payroll broker.
variable "payroll_broker_fqdn" {
  description = "Payroll Broker Container App public FQDN — used as AFD origin for payroll-broker.terianix.ai. Empty string disables payroll-broker AFD resources."
  type        = string
  default     = ""
}

variable "payroll_broker_custom_domain" {
  description = "Custom domain for the Payroll Broker (e.g. payroll-broker.terianix.ai). Must have a CNAME pointing to the AFD endpoint hostname before apply."
  type        = string
  default     = ""
}

variable "legacy_redirect_map" {
  description = "Map of old hostname → new hostname for 301 redirects served at the AFD edge. CNAME records for each old hostname must point to the AFD endpoint hostname. AFD validates domain ownership via the CNAME and issues a managed TLS cert. Empty map = no legacy redirect infrastructure created."
  type        = map(string)
  default     = {}
  # Example:
  # {
  #   "sandbox-awards.terianix.ai" = "sandbox-awards.terianix.ai"
  #   "acme-awards.terianix.ai"    = "acme-awards.terianix.ai"
  #   "demo-awards.terianix.ai"    = "demo-awards.terianix.ai"
  # }
}
