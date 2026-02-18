param location string = 'westus2'
param staticSiteName string = 'award-nomination-frontend'

resource staticSite 'Microsoft.Web/staticSites@2024-11-01' = {
  name: staticSiteName
  location: location
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    repositoryUrl: 'https://github.com/DTerian64/David64_Award_Nominations'
    branch: 'main'
    stagingEnvironmentPolicy: 'Enabled'
    allowConfigFileUpdates: true
    provider: 'GitHub'
    enterpriseGradeCdnStatus: 'Disabled'
  }
}

resource customDomain 'Microsoft.Web/staticSites/customDomains@2024-11-01' = {
  parent: staticSite
  name: 'awards.terian-services.com'
  properties: {
    // validationMethod: 'dns-txt-token'   // usually auto-handled, but can be added if needed
  }
}

// Basic Auth is optional – only include if you actually use it
resource basicAuth 'Microsoft.Web/staticSites/basicAuth@2024-11-01' = {
  parent: staticSite
  name: 'default'
  properties: {
    applicableEnvironmentsMode: 'SpecifiedEnvironments'
    // enabledEnvironments: []   // add environments if needed
  }
}

output defaultHostName string = staticSite.properties.defaultHostname
output customDomainName string = 'awards.terian-services.com'
output staticSiteId string = staticSite.id
