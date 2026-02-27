# environments/prod/backend.tf
# ─────────────────────────────────────────────────────────────────────────────
# Terraform state backend — prod state file
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
    key                  = "dev.tfstate"
  }
}

provider "azurerm" {
  features {}
}
