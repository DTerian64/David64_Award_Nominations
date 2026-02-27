# modules/key-vault/main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Azure Key Vault
#
# Creates:
#   - Key Vault (Standard SKU)
#   - Access policy for deploying user
#   - Access policy for Container Apps managed identities
#   - KV Secrets — values come from terraform.tfvars (gitignored)
#   - Private endpoint → subnet-privatelinks
#   - Private DNS zone group registration
#   - Network rules — deny public, whitelist local IPs
#
# Secret values live in terraform.tfvars (gitignored) and tfstate
# (private Azure blob). They never touch GitHub.
# ─────────────────────────────────────────────────────────────────────────────

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "kv" {
  name                = var.key_vault_name
  resource_group_name = var.resource_group_name
  location            = var.location
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  soft_delete_retention_days    = 90
  purge_protection_enabled      = false
  public_network_access_enabled = var.public_network_access_enabled

  network_acls {
    default_action             = var.public_network_access_enabled ? "Allow" : "Deny"
    bypass                     = "AzureServices"
    ip_rules                   = var.allowed_ips
    virtual_network_subnet_ids = var.aca_subnet_ids
  }

  tags = var.tags
}

# ── Access policy — deploying user ────────────────────────────────────────────
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

# ── Access policy — Container Apps managed identities ────────────────────────
resource "azurerm_key_vault_access_policy" "aca" {
  for_each = toset(var.aca_principal_ids)

  key_vault_id = azurerm_key_vault.kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = each.value

  secret_permissions = ["Get", "List"]
}

# ── Secrets ───────────────────────────────────────────────────────────────────
# Values come from var.secrets map in terraform.tfvars (gitignored)
# Stored in tfstate (private Azure blob) — never in GitHub
resource "azurerm_key_vault_secret" "secrets" {
  for_each = nonsensitive(toset(keys(var.secrets)))

  name         = each.key
  value        = var.secrets[each.key]
  key_vault_id = azurerm_key_vault.kv.id

  # Access policy must exist before secrets can be written
  depends_on = [azurerm_key_vault_access_policy.deployer]

  tags = var.tags
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
