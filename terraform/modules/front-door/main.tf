# modules/front-door/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Front Door (Standard SKU) + WAF Policy
#
# Creates:
#   - AFD Profile (Standard_AzureFrontDoor)
#   - WAF Policy (Prevention mode)
#   - Endpoint
#   - Origin Group (og-award-api) with load balancing matching existing config
#   - Origin East US → internal CAE east via Private Link
#   - Origin West US → internal CAE west via Private Link
#   - Route — forwards all traffic to origin group
#   - Security policy — attaches WAF to endpoint
#
# NOTE: Private Link to internal CAEs requires manual approval after apply.
# Each origin's private endpoint connection must be approved in the portal:
#   Portal → Container App Environment → Networking → Private endpoint connections
# ─────────────────────────────────────────────────────────────────────────────

# ── AFD Profile ───────────────────────────────────────────────────────────────
resource "azurerm_cdn_frontdoor_profile" "afd" {
  name                = var.afd_profile_name
  resource_group_name = var.resource_group_name
  sku_name            = "Standard_AzureFrontDoor"
  tags                = var.tags
}

# ── WAF Policy ────────────────────────────────────────────────────────────────
resource "azurerm_cdn_frontdoor_firewall_policy" "waf" {
  name                = replace("${var.afd_profile_name}waf", "-", "")
  resource_group_name = var.resource_group_name
  sku_name            = azurerm_cdn_frontdoor_profile.afd.sku_name
  enabled             = true
  mode                = "Prevention"

  tags = var.tags
}

# ── Endpoint ──────────────────────────────────────────────────────────────────
resource "azurerm_cdn_frontdoor_endpoint" "endpoint" {
  name                     = var.afd_endpoint_name
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.afd.id
  tags                     = var.tags
}

# ── Origin Group ──────────────────────────────────────────────────────────────
# Matches existing: og-award-api, 4 samples, 3 successful required, 0ms latency
resource "azurerm_cdn_frontdoor_origin_group" "api" {
  name                     = "og-award-api"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.afd.id

  load_balancing {
    sample_size                 = 4
    successful_samples_required = 3
    additional_latency_in_milliseconds = 0
  }

  health_probe {
    interval_in_seconds = 30
    path                = "/health"
    protocol            = "Https"
    request_type        = "HEAD"
  }
}

# ── Origin — East US ──────────────────────────────────────────────────────────
resource "azurerm_cdn_frontdoor_origin" "east" {
  name                          = "origin-award-api-eastus"
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.api.id

  enabled                        = true
  host_name                      = var.cae_east_static_ip
  origin_host_header             = var.cae_east_default_domain
  priority                       = 1
  weight                         = 500
  certificate_name_check_enabled = false

  private_link {
    request_message        = "AFD Private Link request - East"
    location               = var.location_east
    private_link_target_id = var.cae_east_id
  }
}

# ── Origin — West US ──────────────────────────────────────────────────────────
resource "azurerm_cdn_frontdoor_origin" "west" {
  name                          = "origin-award-api-westus"
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.api.id

  enabled                        = true
  host_name                      = var.cae_west_static_ip
  origin_host_header             = var.cae_west_default_domain
  priority                       = 1
  weight                         = 500
  certificate_name_check_enabled = false

  private_link {
    request_message        = "AFD Private Link request - West"
    location               = var.location_west
    private_link_target_id = var.cae_west_id
  }
}

# ── Route ─────────────────────────────────────────────────────────────────────
resource "azurerm_cdn_frontdoor_route" "api" {
  name                          = "route-award-api"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.endpoint.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.api.id
  cdn_frontdoor_origin_ids      = [
    azurerm_cdn_frontdoor_origin.east.id,
    azurerm_cdn_frontdoor_origin.west.id,
  ]

  enabled                = true
  forwarding_protocol    = "HttpsOnly"
  https_redirect_enabled = true
  patterns_to_match      = ["/*"]
  supported_protocols    = ["Http", "Https"]
}

# ── Security Policy — attach WAF to endpoint ──────────────────────────────────
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
