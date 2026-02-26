
# main.tf
# ─────────────────────────────────────────────────────────────────────────────
# Provider configuration and Terraform backend
# ─────────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }

  backend "azurerm" {
    resource_group_name  = "rg_award_nomination"
    storage_account_name = "awardnominationmodels"
    container_name       = "tfstate"
    key                  = "award-nomination-network.tfstate"
  }
}

provider "azurerm" {
  features {}
}

# ─────────────────────────────────────────────────────────────────────────────
# Data sources — reference existing resources by name
# ─────────────────────────────────────────────────────────────────────────────

data "azurerm_resource_group" "rg" {
  name = var.resource_group_name
}

data "azurerm_mssql_server" "sql" {
  name                = "david64-sql"
  resource_group_name = var.resource_group_name
}

data "azurerm_storage_account" "blob" {
  name                = "awardnominationmodels"
  resource_group_name = var.resource_group_name
}

data "azurerm_key_vault" "kv" {
  name                = "kv-awardnominations"
  resource_group_name = var.resource_group_name
}

data "azurerm_cognitive_account" "openai" {
  name                = "award-nomination-open-AI"
  resource_group_name = var.resource_group_name
}

data "azurerm_container_registry" "acr" {
  name                = "acrawardnomination"
  resource_group_name = var.resource_group_name
}
