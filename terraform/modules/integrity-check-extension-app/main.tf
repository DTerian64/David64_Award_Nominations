# Service-specific composition for asynchronous integrity extensions.
# This worker has no routing authority and receives no Service Bus sender role.

data "azurerm_client_config" "current" {}

resource "azurerm_user_assigned_identity" "this" {
  name                = "id-award-integrity-check-extension-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}

resource "azurerm_key_vault_access_policy" "this" {
  key_vault_id = var.key_vault_id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.this.principal_id

  secret_permissions = ["Get", "List"]
}

resource "azurerm_role_assignment" "blob_reader" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}

resource "azurerm_role_assignment" "service_bus_receiver" {
  scope                = var.service_bus_topic_id
  role_definition_name = "Azure Service Bus Data Receiver"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}

module "container_app" {
  source = "../auxiliary-container-app"

  resource_group_name          = var.resource_group_name
  location                     = var.location
  app_name                     = var.app_name
  environment                  = var.environment
  container_app_environment_id = var.container_app_environment_id
  workload_profile_name        = var.workload_profile_name

  auxiliary_identity_id        = azurerm_user_assigned_identity.this.id
  auxiliary_identity_client_id = azurerm_user_assigned_identity.this.client_id

  acr_login_server   = var.acr_login_server
  acr_admin_username = var.acr_admin_username
  acr_admin_password = var.acr_admin_password

  service_bus_fqns              = var.service_bus_fqns
  service_bus_topic_name        = var.service_bus_topic_name
  service_bus_subscription_name = var.service_bus_subscription_name
  key_vault_uri                 = var.key_vault_uri

  min_replicas       = var.min_replicas
  max_replicas       = var.max_replicas
  keda_message_count = 1
  cpu                = var.cpu
  memory             = var.memory

  environment_variables = [
    { name = "AZURE_STORAGE_ACCOUNT", value = var.storage_account_name },
    { name = "MODEL_CONTAINER", value = var.model_container_name },
    { name = "CONTAINER_APP_NAME", value = var.app_name },
    { name = "OTEL_LOGS_EXPORTER", value = "None" },
    { name = "OTEL_TRACES_SAMPLER", value = "microsoft.fixed_percentage" },
    { name = "OTEL_TRACES_SAMPLER_ARG", value = "0.2" },
  ]

  kv_secret_references = [
    { env_name = "SQL_SERVER", kv_secret_name = "SQL-SERVER" },
    { env_name = "SQL_DATABASE", kv_secret_name = "SQL-DATABASE" },
    { env_name = "APPLICATIONINSIGHTS_CONNECTION_STRING", kv_secret_name = "APPINSIGHTS-CONNECTION-STRING-BACKEND" },
  ]

  tags = var.tags

  depends_on = [
    azurerm_key_vault_access_policy.this,
    azurerm_role_assignment.blob_reader,
    azurerm_role_assignment.service_bus_receiver,
  ]
}
