# modules/sql/outputs.tf

output "server_id" {
  description = "SQL Server resource ID"
  value       = azurerm_mssql_server.sql.id
}

output "server_name" {
  description = "SQL Server name"
  value       = azurerm_mssql_server.sql.name
}

output "server_fqdn" {
  description = "Fully qualified domain name — use in connection strings"
  value       = azurerm_mssql_server.sql.fully_qualified_domain_name
}

output "database_name" {
  description = "SQL Database name"
  value       = azurerm_mssql_database.db.name
}

output "database_id" {
  description = "SQL Database resource ID"
  value       = azurerm_mssql_database.db.id
}

output "connection_string" {
  description = "ADO.NET connection string — inject into Container App env vars"
  value       = "Server=tcp:${azurerm_mssql_server.sql.fully_qualified_domain_name},1433;Initial Catalog=${azurerm_mssql_database.db.name};Persist Security Info=False;User ID=${var.admin_login};Password=${var.admin_password};MultipleActiveResultSets=False;Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;"
  sensitive   = true
}

output "private_endpoint_ip" {
  description = "Private IP address of the SQL private endpoint"
  value       = azurerm_private_endpoint.sql.private_service_connection[0].private_ip_address
}
