# modules/front-door/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Front Door Standard + WAF Policy
#
# Origins connect to Container App Environments via public hostname.
# AFD → public CAE domain (HTTPS) → Container App
#
# NOTE: After apply, no Private Link approval needed.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_cdn_frontdoor_profile" "afd" {
  name                = var.afd_profile_name
  resource_group_name = var.resource_group_name
  sku_name            = "Standard_AzureFrontDoor"
  tags                = var.tags
}

resource "azurerm_cdn_frontdoor_firewall_policy" "waf" {
  name                = replace("${var.afd_profile_name}waf", "-", "")
  resource_group_name = var.resource_group_name
  sku_name            = azurerm_cdn_frontdoor_profile.afd.sku_name
  enabled             = true
  mode                = "Prevention"
  tags                = var.tags
}

resource "azurerm_cdn_frontdoor_endpoint" "endpoint" {
  name                     = var.afd_endpoint_name
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.afd.id
  tags                     = var.tags
}

resource "azurerm_cdn_frontdoor_origin_group" "api" {
  name                     = "og-award-api"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.afd.id

  load_balancing {
    sample_size                        = 4
    successful_samples_required        = 3
    additional_latency_in_milliseconds = 0
  }

  health_probe {
    interval_in_seconds = 30
    path                = "/health"
    protocol            = "Https"
    request_type        = "HEAD"
  }
}

resource "azurerm_cdn_frontdoor_origin" "primary" {
  name                          = "origin-award-api-primary"
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.api.id

  enabled                        = true
  host_name                      = var.container_app_primary_fqdn
  origin_host_header             = var.container_app_primary_fqdn
  priority                       = 1
  weight                         = 500
  certificate_name_check_enabled = true
}

resource "azurerm_cdn_frontdoor_origin" "secondary" {
  name                          = "origin-award-api-secondary"
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.api.id

  enabled                        = true
  host_name                      = var.container_app_secondary_fqdn
  origin_host_header             = var.container_app_secondary_fqdn
  priority                       = 1
  weight                         = 500
  certificate_name_check_enabled = true
}

resource "azurerm_cdn_frontdoor_route" "api" {
  name                          = "route-award-api"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.endpoint.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.api.id
  cdn_frontdoor_origin_ids = [
    azurerm_cdn_frontdoor_origin.primary.id,
    azurerm_cdn_frontdoor_origin.secondary.id,
  ]

  enabled                    = true
  forwarding_protocol        = "HttpsOnly"
  https_redirect_enabled     = true
  patterns_to_match          = ["/*"]
  supported_protocols        = ["Http", "Https"]
  cdn_frontdoor_rule_set_ids = [azurerm_cdn_frontdoor_rule_set.cors.id]

  depends_on = [azurerm_cdn_frontdoor_rule_set.cors]
}

# ── Payroll Broker origin group + origin + custom domain + route ──────────────
# Created only when var.payroll_broker_fqdn is non-empty (count pattern).
# This keeps all payroll-broker AFD resources optional — environments without
# the payroll broker provisioned skip these resources entirely.
#
# Route separation:
#   route-award-api    → sandbox-awards.terianix.ai  → Backend ACA (patterns: /*)
#   route-payroll-broker → payroll-broker.terianix.ai → Payroll Broker ACA (patterns: /*)
# Because each route is bound to its own custom domain, AFD dispatches by
# Host header — no path-prefix overlap or ordering conflict.

resource "azurerm_cdn_frontdoor_origin_group" "payroll_broker" {
  count                    = var.payroll_broker_custom_domain != "" ? 1 : 0
  name                     = "og-payroll-broker"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.afd.id

  load_balancing {
    sample_size                        = 4
    successful_samples_required        = 3
    additional_latency_in_milliseconds = 0
  }

  health_probe {
    interval_in_seconds = 30
    path                = "/health"
    protocol            = "Https"
    request_type        = "HEAD"
  }
}

resource "azurerm_cdn_frontdoor_origin" "payroll_broker" {
  count                         = var.payroll_broker_custom_domain != "" ? 1 : 0
  name                          = "origin-payroll-broker"
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.payroll_broker[0].id

  enabled                        = true
  host_name                      = var.payroll_broker_fqdn
  origin_host_header             = var.payroll_broker_fqdn
  priority                       = 1
  weight                         = 1000
  certificate_name_check_enabled = true
}

# Custom domain — payroll-broker.terianix.ai
# DNS prerequisite: CNAME payroll-broker.terianix.ai → AFD endpoint hostname
# must exist before apply so Azure can validate domain ownership.
resource "azurerm_cdn_frontdoor_custom_domain" "payroll_broker" {
  count                    = var.payroll_broker_custom_domain != "" ? 1 : 0
  name                     = replace(split(".", var.payroll_broker_custom_domain)[0], "-", "")
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.afd.id
  host_name                = var.payroll_broker_custom_domain

  tls {
    certificate_type    = "ManagedCertificate"
    minimum_tls_version = "TLS12"
  }
}

# Route — binds payroll-broker.terianix.ai to the payroll-broker origin group.
# link_to_default_domain = false ensures the AFD default *.azurefd.net domain
# does NOT also route to the payroll broker (keeps the two routes cleanly separated).
resource "azurerm_cdn_frontdoor_route" "payroll_broker" {
  count                         = var.payroll_broker_custom_domain != "" ? 1 : 0
  name                          = "route-payroll-broker"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.endpoint.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.payroll_broker[0].id
  cdn_frontdoor_origin_ids      = [azurerm_cdn_frontdoor_origin.payroll_broker[0].id]
  cdn_frontdoor_custom_domain_ids = [
    azurerm_cdn_frontdoor_custom_domain.payroll_broker[0].id
  ]
  cdn_frontdoor_rule_set_ids = [azurerm_cdn_frontdoor_rule_set.cors.id]

  enabled                = true
  forwarding_protocol    = "HttpsOnly"
  https_redirect_enabled = true
  patterns_to_match      = ["/*"]
  supported_protocols    = ["Http", "Https"]
  link_to_default_domain = false

  depends_on = [
    azurerm_cdn_frontdoor_rule_set.cors,
    azurerm_cdn_frontdoor_custom_domain.payroll_broker,
  ]
}

# ── CORS Rules Engine ─────────────────────────────────────────────────────────
# AFD strips Access-Control-Allow-Origin from backend responses by default.
# These rules re-add the required CORS headers at the CDN layer for any request
# that includes an Origin header (i.e. all cross-origin browser requests).

resource "azurerm_cdn_frontdoor_rule_set" "cors" {
  name                     = "corsruleset"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.afd.id
}

resource "azurerm_cdn_frontdoor_rule" "cors_headers" {
  name                      = "AddCORSHeaders"
  cdn_frontdoor_rule_set_id = azurerm_cdn_frontdoor_rule_set.cors.id
  order                     = 1
  behavior_on_match         = "Continue"

  conditions {
    request_header_condition {
      header_name      = "Origin"
      operator         = "Any"
      negate_condition = false
    }
  }

  actions {
    # Echo the request Origin back — required when allow_credentials=true (cannot use *)
    response_header_action {
      header_action = "Overwrite"
      header_name   = "Access-Control-Allow-Origin"
      value         = "{http_req_header_Origin}"
    }
    response_header_action {
      header_action = "Overwrite"
      header_name   = "Access-Control-Allow-Credentials"
      value         = "true"
    }
    response_header_action {
      header_action = "Overwrite"
      header_name   = "Access-Control-Allow-Methods"
      value         = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
    }
    response_header_action {
      header_action = "Overwrite"
      header_name   = "Access-Control-Allow-Headers"
      value         = "Authorization, Content-Type, Accept, X-Requested-With, X-Impersonate-User, traceparent, tracestate, baggage, Request-Id, Correlation-Context"
    }
    response_header_action {
      header_action = "Overwrite"
      header_name   = "Vary"
      value         = "Origin"
    }
  }

  depends_on = [azurerm_cdn_frontdoor_rule_set.cors]
}

resource "azurerm_cdn_frontdoor_security_policy" "waf" {
  name                     = "security-policy-waf"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.afd.id

  security_policies {
    firewall {
      cdn_frontdoor_firewall_policy_id = azurerm_cdn_frontdoor_firewall_policy.waf.id

      association {
        patterns_to_match = ["/*"]

        domain {
          cdn_frontdoor_domain_id = azurerm_cdn_frontdoor_endpoint.endpoint.id
        }

        # Apply WAF to legacy redirect domains so inbound traffic on old hostnames
        # is inspected before the edge issues the 301 redirect.
        dynamic "domain" {
          for_each = azurerm_cdn_frontdoor_custom_domain.legacy
          content {
            cdn_frontdoor_domain_id = domain.value.id
          }
        }

        # Apply WAF to the payroll-broker custom domain — Gusto webhooks and
        # OAuth callbacks must pass through WAF before reaching the broker.
        dynamic "domain" {
          for_each = azurerm_cdn_frontdoor_custom_domain.payroll_broker
          content {
            cdn_frontdoor_domain_id = domain.value.id
          }
        }
      }
    }
  }
}

# ── Legacy domain redirects (terianix.ai → terianix.ai) ───────────────
# Each entry in var.legacy_redirect_map:
#   1. Registers the old hostname as an AFD custom domain (managed TLS cert).
#   2. Uses the AFD Rules Engine to issue a 301 redirect to the mapped new
#      hostname, preserving path and query string — no round-trip to origin.
#
# DNS prerequisite (managed in sandbox/main.tf):
#   Each old subdomain CNAME must already point to the AFD endpoint hostname
#   before `terraform apply`. Azure validates subdomain ownership via the CNAME;
#   no separate _dnsauth TXT record is required for subdomains.
#
# NOTE: AFD TLS cert issuance can take up to 30 minutes. If validation times
#   out on the first apply, re-run `terraform apply` after DNS has propagated.

resource "azurerm_cdn_frontdoor_custom_domain" "legacy" {
  for_each = var.legacy_redirect_map

  # Name: alphanumeric only, derived from the subdomain label (e.g. "sandboxawards")
  name                     = replace(split(".", each.key)[0], "-", "")
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.afd.id
  host_name                = each.key

  tls {
    certificate_type    = "ManagedCertificate"
    minimum_tls_version = "TLS12"
  }
}

resource "azurerm_cdn_frontdoor_rule_set" "legacy_redirect" {
  count                    = length(var.legacy_redirect_map) > 0 ? 1 : 0
  name                     = "legacyredirect"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.afd.id
}

resource "azurerm_cdn_frontdoor_rule" "legacy_redirect" {
  for_each = var.legacy_redirect_map

  # Rule name: alphanumeric only (e.g. "redirectsandboxawards")
  name                      = "redirect${replace(split(".", each.key)[0], "-", "")}"
  cdn_frontdoor_rule_set_id = azurerm_cdn_frontdoor_rule_set.legacy_redirect[0].id
  # sort() keeps order deterministic across plan/apply cycles
  order             = index(sort(keys(var.legacy_redirect_map)), each.key) + 1
  behavior_on_match = "Stop"

  conditions {
    host_name_condition {
      operator         = "Equal"
      negate_condition = false
      match_values     = [each.key]
    }
  }

  actions {
    url_redirect_action {
      redirect_type        = "PermanentRedirect" # 301
      redirect_protocol    = "Https"
      destination_hostname = each.value          # e.g. "sandbox-awards.terianix.ai"
      # destination_path and query_string omitted → AFD preserves originals
    }
  }

  depends_on = [azurerm_cdn_frontdoor_rule_set.legacy_redirect]
}

resource "azurerm_cdn_frontdoor_route" "legacy_redirect" {
  count = length(var.legacy_redirect_map) > 0 ? 1 : 0

  name                          = "route-legacy-redirect"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.endpoint.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.api.id
  cdn_frontdoor_origin_ids = [
    azurerm_cdn_frontdoor_origin.primary.id,
  ]
  cdn_frontdoor_custom_domain_ids = [
    for d in azurerm_cdn_frontdoor_custom_domain.legacy : d.id
  ]
  cdn_frontdoor_rule_set_ids = [azurerm_cdn_frontdoor_rule_set.legacy_redirect[0].id]

  enabled                = true
  forwarding_protocol    = "HttpsOnly"
  https_redirect_enabled = false  # rules engine issues the redirect; disabling AFD's own HTTP→HTTPS avoids double-redirect
  patterns_to_match      = ["/*"]
  supported_protocols    = ["Http", "Https"]
  link_to_default_domain = false

  depends_on = [
    azurerm_cdn_frontdoor_rule_set.legacy_redirect,
    azurerm_cdn_frontdoor_custom_domain.legacy,
  ]
}
