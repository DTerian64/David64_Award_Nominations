# modules/key-vault/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Key Vault
#
# Creates:
#   - Key Vault (Standard SKU — matches kv-awardnominations)
#   - Access policy for the Container Apps managed identity
#   - Access policy for the deploying user/service principal
#   - Private endpoint → subnet-privatelinks
#   - Private DNS zone group registration
#   - Network rules — deny public, whitelist local IPs
#
# Secrets are NOT created here — they are managed outside Terraform
# (manually via portal or az cli) to avoid sensitive values in state.
# ─────────────────────────────────────────────────────────────────────────────

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "kv" {
  name                = var.key_vault_name
  resource_group_name = var.resource_group_name
  location            = var.location
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  # Matches existing: 90 day soft delete retention
  soft_delete_retention_days = 90
  purge_protection_enabled   = false

  # Start with public access enabled — lock down after private endpoint confirmed
  public_network_access_enabled = var.public_network_access_enabled

  network_acls {
    default_action             = var.public_network_access_enabled ? "Allow" : "Deny"
    bypass                     = ["AzureServices"]
    ip_rules                   = var.allowed_ips
    virtual_network_subnet_ids = var.aca_subnet_ids
  }

  tags = var.tags
}

# ── Access policy — deploying user ────────────────────────────────────────────
# Gives the person running terraform apply full access to manage secrets
resource "azurerm_key_vault_access_policy" "deployer" {
  key_vault_id = azurerm_key_vault.kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = data.azurerm_client_config.current.object_id

  secret_permissions = [
    "Get", "List", "Set", "Delete", "Purge", "Recover", "Backup", "Restore"
  ]

  key_permissions = [
    "Get", "List", "Create", "Delete", "Purge", "Recover"
  ]
}

# ── Access policy — Container Apps managed identity ───────────────────────────
# Gives ACAs read access to secrets at runtime
resource "azurerm_key_vault_access_policy" "aca" {
  for_each = toset(var.aca_principal_ids)

  key_vault_id = azurerm_key_vault.kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = each.value

  secret_permissions = ["Get", "List"]
}

# ── Private endpoint ──────────────────────────────────────────────────────────
resource "azurerm_private_endpoint" "kv" {
  name                = "pe-${var.key_vault_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-${var.key_vault_name}"
    private_connection_resource_id = azurerm_key_vault.kv.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "dns-group-kv"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }
}
