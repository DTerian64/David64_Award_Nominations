# environments/dev/backend.tf

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.116"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.47"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  backend "azurerm" {
    resource_group_name  = "rg_platform"
    storage_account_name = "awardnomplatform"
    container_name       = "tfstate"
    key                  = "sandbox.tfstate"
  }
}

provider "azurerm" {
  features {
    resource_group {
      # Allow Terraform to delete the RG even if Azure-side resources still exist
      # (e.g. orphaned NICs from failed partial applies). Azure API will force-delete all children.
      prevent_deletion_if_contains_resources = false
    }

    key_vault {
      # Purge soft-deleted Key Vault on destroy so the name is immediately reusable.
      # Safe because purge_protection_enabled = false on the vault resource.
      purge_soft_delete_on_destroy    = true
      recover_soft_deleted_key_vaults = false
    }

    cognitive_account {
      # Purge soft-deleted OpenAI / Cognitive Services account on destroy
      # so the name is immediately reusable on the next apply.
      purge_soft_delete_on_destroy = true
    }
  }
}

provider "azuread" {}
