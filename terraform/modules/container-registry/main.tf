# modules/container-registry/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Container Registry (ACR)
#
# Creates:
#   - ACR (Basic SKU — matches existing acrawardnomination)
#   - Private endpoint → subnet-privatelinks
#   - Private DNS zone group registration
#
# GitHub Actions pushes images here.
# Container Apps pull images from here via private endpoint.
#
# NOTE: admin_enabled = true is required for Container Apps to pull images
# without managed identity configuration. If you later configure managed
# identity on the ACAs, set admin_enabled = false for better security.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_container_registry" "acr" {
  name                = var.acr_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = var.sku
  admin_enabled       = var.admin_enabled

  # Disable public access once private endpoint is confirmed working
  public_network_access_enabled = var.public_network_access_enabled

  tags = var.tags
}

# ── Private endpoint ──────────────────────────────────────────────────────────
resource "azurerm_private_endpoint" "acr" {
  name                = "pe-${var.acr_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-${var.acr_name}"
    private_connection_resource_id = azurerm_container_registry.acr.id
    subresource_names              = ["registry"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "dns-group-acr"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }
}
