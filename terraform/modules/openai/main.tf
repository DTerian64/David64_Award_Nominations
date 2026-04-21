# modules/openai/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure OpenAI Service
#
# Creates:
#   - Cognitive Services account (kind=OpenAI, SKU=S0)
#   - Model deployment: gpt-4.1, GlobalStandard, 150K TPM
#   - Private endpoint → subnet-privatelinks
#   - Private DNS zone group registration
#   - Network rules — whitelist local IPs
#
# NOTE: Azure OpenAI requires capacity quota approval per region.
# If deploying to a new subscription, request quota for gpt-4.1
# GlobalStandard in East US before running terraform apply.
# Portal: Azure OpenAI → Quotas → Request increase
#
# Cross-region private endpoint:
# When openai_location differs from location_primary (the subnet's region),
# private_endpoint_location must be set to match the subnet's region.
# The endpoint NIC lives in the subnet's region; the connection target (OpenAI)
# can be in any region — Azure routes traffic over its backbone.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # Private endpoint must be co-located with its subnet, not necessarily with
  # the OpenAI account. Fall back to var.location when not overridden (same-region case).
  pe_location = var.private_endpoint_location != "" ? var.private_endpoint_location : var.location
}

resource "azurerm_cognitive_account" "openai" {
  name                = var.openai_name
  resource_group_name = var.resource_group_name
  location            = var.location
  kind                = "OpenAI"
  sku_name            = "S0"

  custom_subdomain_name         = var.openai_name
  public_network_access_enabled = var.public_network_access_enabled

  network_acls {
    default_action = var.public_network_access_enabled ? "Allow" : "Deny"
    ip_rules       = var.allowed_ips
  }

  tags = var.tags
}

# ── Model deployment ──────────────────────────────────────────────────────────
resource "azurerm_cognitive_deployment" "gpt4" {
  name                 = var.model_deployment_name
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = var.model_name
    version = var.model_version
  }

  scale {
    type     = "GlobalStandard"
    capacity = var.model_capacity_tpm
  }
}

# ── Private endpoint ──────────────────────────────────────────────────────────
resource "azurerm_private_endpoint" "openai" {
  name                = "pe-${var.openai_name}"
  location            = local.pe_location   # subnet's region, not necessarily OpenAI's region
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-${var.openai_name}"
    private_connection_resource_id = azurerm_cognitive_account.openai.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "dns-group-openai"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }

  depends_on = [azurerm_cognitive_deployment.gpt4]
}
