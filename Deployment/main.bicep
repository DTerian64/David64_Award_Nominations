targetScope = 'resourceGroup'

param locationEast string = 'eastus'
param locationWest string = 'westus2'

@secure()
param sqlAdminPassword string
param sqlAdminLogin string = 'dterian'

@secure()
param domains_terian_services_com_email string
@secure()
param domains_terian_services_com_nameFirst string
@secure()
param domains_terian_services_com_nameLast string
@secure()
param domains_terian_services_com_phone string
@secure()
param domains_terian_services_com_email_1 string
@secure()
param domains_terian_services_com_nameFirst_1 string
@secure()
param domains_terian_services_com_nameLast_1 string
@secure()
param domains_terian_services_com_phone_1 string
@secure()
param domains_terian_services_com_email_2 string
@secure()
param domains_terian_services_com_nameFirst_2 string
@secure()
param domains_terian_services_com_nameLast_2 string
@secure()
param domains_terian_services_com_phone_2 string
@secure()
param domains_terian_services_com_email_3 string
@secure()
param domains_terian_services_com_nameFirst_3 string
@secure()
param domains_terian_services_com_nameLast_3 string
@secure()
param domains_terian_services_com_phone_3 string

param acrName string = 'acrawardnomination'
param kvName string = 'kv-awardnominations'
param sqlServerName string = 'david64-sql'
param storageName string = 'awardnominationmodels'
param dnsZoneName string = 'terian-services.com'
param staticSiteName string = 'award-nomination-frontend'
param frontDoorName string = 'Award-Nomination-ADF'
param frontDoorEndpoint string = ''   // empty on first deploy; pipeline passes real value on subsequent deploys
@secure()
param emailActionSecretKey string
module monitoring './modules/monitoring.bicep' = {
  name: 'monitoring'
  params: { locationEast: locationEast, locationWest: locationWest }
}

module networking './modules/networking.bicep' = {
  name: 'networking'
  params: {
    dnsZoneName: dnsZoneName
    domainName: dnsZoneName
    email: domains_terian_services_com_email
    nameFirst: domains_terian_services_com_nameFirst
    nameLast: domains_terian_services_com_nameLast
    phone: domains_terian_services_com_phone
    email_1: domains_terian_services_com_email_1
    nameFirst_1: domains_terian_services_com_nameFirst_1
    nameLast_1: domains_terian_services_com_nameLast_1
    phone_1: domains_terian_services_com_phone_1
    email_2: domains_terian_services_com_email_2
    nameFirst_2: domains_terian_services_com_nameFirst_2
    nameLast_2: domains_terian_services_com_nameLast_2
    phone_2: domains_terian_services_com_phone_2
    email_3: domains_terian_services_com_email_3
    nameFirst_3: domains_terian_services_com_nameFirst_3
    nameLast_3: domains_terian_services_com_nameLast_3
    phone_3: domains_terian_services_com_phone_3
  }
}

module secrets './modules/secrets.bicep' = {
  name: 'secrets'
  params: { kvName: kvName, location: locationEast }
}

module data './modules/data.bicep' = {
  name: 'data'
  params: {
    location: locationWest
    sqlServerName: sqlServerName
    sqlAdminLogin: sqlAdminLogin
    sqlAdminPassword: sqlAdminPassword
    storageName: storageName
  }
  dependsOn: [secrets]
}

module containerRuntime './modules/container-runtime.bicep' = {
  name: 'containerRuntime'
  params: {
    locationEast: locationEast
    locationWest: locationWest
    acrName: acrName
    kvName: kvName
    laEastCustomerId: monitoring.outputs.laEastCustomerId
    laWestCustomerId: monitoring.outputs.laWestCustomerId
    laEastResourceId: monitoring.outputs.laEastId
    laWestResourceId: monitoring.outputs.laWestId
    frontDoorEndpoint: frontDoorEndpoint
    emailActionSecretKey: emailActionSecretKey
  }
  dependsOn: [data, monitoring]
}

module frontend './modules/frontend.bicep' = {
  name: 'frontend'
  params: { location: locationWest, staticSiteName: staticSiteName }
}

module frontDoor './modules/frontdoor.bicep' = {
  name: 'frontDoor'
  params: {
    frontDoorName: frontDoorName
    originEastHostname: containerRuntime.outputs.apiEastFqdn
    originWestHostname: containerRuntime.outputs.apiWestFqdn
  }
  dependsOn: [containerRuntime, frontend]
}

output frontDoorEndpoint string = frontDoor.outputs.endpoint
output staticSiteDefaultHost string = frontend.outputs.defaultHostName
output apiEastFqdn string = containerRuntime.outputs.apiEastFqdn
output apiWestFqdn string = containerRuntime.outputs.apiWestFqdn
