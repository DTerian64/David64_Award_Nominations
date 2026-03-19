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
      value         = "Authorization, Content-Type, Accept, X-Requested-With"
    }
    response_header_action {
      header_action = "Overwrite"
      header_name   = "Access-Control-Expose-Headers"
      value         = "Content-Length, Content-Type"
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
      }
    }
  }
}
