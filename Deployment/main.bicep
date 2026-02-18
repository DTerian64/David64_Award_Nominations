targetScope = 'resourceGroup'

param locationEast string = 'eastus'
param locationWest string = 'westus2'

@secure() param sqlAdminPassword string
param sqlAdminLogin string = 'dterian'

// Domain contact params (copy all 12 @secure() ones from your original file)
@secure() param domains_terian_services_com_email string
@secure() param domains_terian_services_com_nameFirst string
@secure() param domains_terian_services_com_nameLast string
@secure() param domains_terian_services_com_phone string
@secure() param domains_terian_services_com_email_1 string
@secure() param domains_terian_services_com_nameFirst_1 string
@secure() param domains_terian_services_com_nameLast_1 string
@secure() param domains_terian_services_com_phone_1 string
@secure() param domains_terian_services_com_email_2 string
@secure() param domains_terian_services_com_nameFirst_2 string
@secure() param domains_terian_services_com_nameLast_2 string
@secure() param domains_terian_services_com_phone_2 string
@secure() param domains_terian_services_com_email_3 string
@secure() param domains_terian_services_com_nameFirst_3 string
@secure() param domains_terian_services_com_nameLast_3 string
@secure() param domains_terian_services_com_phone_3 string

// Resource names (you can parameterize more later)
param acrName string = 'acrawardnomination'
param kvName string = 'kv-awardnominations'
param sqlServerName string = 'david64-sql'
param storageName string = 'awardnominationmodels'
param dnsZoneName string = 'terian-services.com'
param staticSiteName string = 'award-nomination-frontend'
param frontDoorName string = 'Award-Nomination-ADF'
param loadTestName string = 'award-nomination-load-testing'

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
    // pass the other 11 domain contact params the same way
    nameFirst: domains_terian_services_com_nameFirst
    // ... (add them all)
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
  }
  dependsOn: [data, monitoring]
}

module frontend './modules/frontend.bicep' = {
  name: 'frontend'
  params: { location: locationWest, staticSiteName: staticSiteName }
}

module frontDoor './modules/frontdoor.bicep' = {
  name: 'frontDoor'
  params: { frontDoorName: frontDoorName }
  dependsOn: [containerRuntime, frontend]
}

output frontDoorEndpoint string = frontDoor.outputs.endpoint
output staticSiteDefaultHost string = frontend.outputs.defaultHostName
