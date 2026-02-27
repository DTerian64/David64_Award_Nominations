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
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_cognitive_account" "openai" {
  name                = var.openai_name
  resource_group_name = var.resource_group_name
  location            = var.location
  kind                = "OpenAI"
  sku_name            = "S0"

  public_network_access_enabled = var.public_network_access_enabled

  network_acls {
    default_action = var.public_network_access_enabled ? "Allow" : "Deny"
    bypass         = ["AzureServices"]
    ip_rules       = var.allowed_ips
  }

  tags = var.tags
}

# ── Model deployment ──────────────────────────────────────────────────────────
# Matches existing: gpt-4.1, GlobalStandard, 150K TPM capacity
resource "azurerm_cognitive_deployment" "gpt4" {
  name                 = var.model_deployment_name
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = var.model_name
    version = var.model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.model_capacity_tpm
  }
}

# ── Private endpoint ──────────────────────────────────────────────────────────
resource "azurerm_private_endpoint" "openai" {
  name                = "pe-${var.openai_name}"
  location            = var.location
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
}
