# modules/sql-access/outputs.tf

output "readwrite_group_object_id" {
  description = "Object ID of the runtime read/write group."
  value       = azuread_group.sql_app_readwrite.object_id
}

output "readwrite_group_name" {
  description = "Display name of the runtime read/write group."
  value       = azuread_group.sql_app_readwrite.display_name
}

output "migrations_group_object_id" {
  description = "Object ID of the schema-migrations group (the migration job's MI joins this)."
  value       = azuread_group.sql_migrations.object_id
}

output "migrations_group_name" {
  description = "Display name of the schema-migrations group."
  value       = azuread_group.sql_migrations.display_name
}

output "admins_group_object_id" {
  description = "Object ID of the SQL admins group -- feed into the sql module as the Entra admin."
  value       = azuread_group.sql_admins.object_id
}

output "admins_group_name" {
  description = "Display name of the SQL admins group -- used as the Entra admin login_username."
  value       = azuread_group.sql_admins.display_name
}
