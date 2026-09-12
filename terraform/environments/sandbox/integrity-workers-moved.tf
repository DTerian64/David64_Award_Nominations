# Preserve all live sandbox resource identities while extracting the two
# service-specific modules from main.tf. These declarations are safe to retain.

moved {
  from = azurerm_user_assigned_identity.integrity_check
  to   = module.integrity_check.azurerm_user_assigned_identity.this
}

moved {
  from = azurerm_key_vault_access_policy.integrity_check
  to   = module.integrity_check.azurerm_key_vault_access_policy.this
}

moved {
  from = azurerm_role_assignment.integrity_check_blob_reader
  to   = module.integrity_check.azurerm_role_assignment.blob_reader
}

moved {
  from = azurerm_role_assignment.integrity_check_sb_sender
  to   = module.integrity_check.azurerm_role_assignment.service_bus_sender
}

moved {
  from = azurerm_role_assignment.integrity_check_openai_user
  to   = module.integrity_check.azurerm_role_assignment.openai_user
}

moved {
  from = module.service_bus.azurerm_role_assignment.receiver["integrity-check"]
  to   = module.integrity_check.azurerm_role_assignment.service_bus_receiver
}

moved {
  from = module.integrity_check.azurerm_container_app.auxiliary
  to   = module.integrity_check.module.container_app.azurerm_container_app.auxiliary
}

moved {
  from = azurerm_user_assigned_identity.integrity_check_extension
  to   = module.integrity_check_extension.azurerm_user_assigned_identity.this
}

moved {
  from = azurerm_key_vault_access_policy.integrity_check_extension
  to   = module.integrity_check_extension.azurerm_key_vault_access_policy.this
}

moved {
  from = azurerm_role_assignment.integrity_check_extension_blob_reader
  to   = module.integrity_check_extension.azurerm_role_assignment.blob_reader
}

moved {
  from = module.service_bus.azurerm_role_assignment.receiver["integrity-check-extension"]
  to   = module.integrity_check_extension.azurerm_role_assignment.service_bus_receiver
}

moved {
  from = module.integrity_check_extension.azurerm_container_app.auxiliary
  to   = module.integrity_check_extension.module.container_app.azurerm_container_app.auxiliary
}
