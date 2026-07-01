# modules/payroll-broker/outputs.tf

output "container_app_id" {
  description = "Payroll Broker Container App resource ID"
  value       = azurerm_container_app.payroll_broker.id
}

output "container_app_name" {
  description = "Payroll Broker Container App name — used by GitHub Actions to update the image after deploy"
  value       = azurerm_container_app.payroll_broker.name
}

output "fqdn" {
  description = "Payroll Broker public FQDN — passed to the front-door module as origin hostname (e.g. award-payroll-broker-sandbox.ambitiousflower-xxxx.westus2.azurecontainerapps.io)"
  value       = azurerm_container_app.payroll_broker.ingress[0].fqdn
}
