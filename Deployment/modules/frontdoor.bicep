param frontDoorName string = 'Award-Nomination-ADF'
param location string = 'global'   // Front Door is global, but parameter for consistency

resource frontDoorProfile 'Microsoft.Cdn/profiles@2025-04-15' = {
  name: frontDoorName
  location: location
  sku: {
    name: 'Standard_AzureFrontDoor'
  }
  kind: 'frontdoor'
  properties: {
    originResponseTimeoutSeconds: 30
  }
}

resource originGroup 'Microsoft.Cdn/profiles/origingroups@2025-04-15' = {
  parent: frontDoorProfile
  name: 'og-award-api'
  properties: {
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
    }
    healthProbeSettings: {
      probePath: '/'
      probeRequestType: 'HEAD'
      probeProtocol: 'Https'
      probeIntervalInSeconds: 100
    }
  }
}

resource originEast 'Microsoft.Cdn/profiles/origingroups/origins@2025-04-15' = {
  parent: originGroup
  name: 'eastus'
  properties: {
    hostName: 'award-api-eastus.<your-unique-suffix>.eastus.azurecontainerapps.io'   // ← replace with actual hostname after first deploy
    httpPort: 80
    httpsPort: 443
    originHostHeader: 'award-api-eastus.<your-unique-suffix>.eastus.azurecontainerapps.io'
    priority: 1
    weight: 50
    enabledState: 'Enabled'
    enforceCertificateNameCheck: true
  }
}

resource originWest 'Microsoft.Cdn/profiles/origingroups/origins@2025-04-15' = {
  parent: originGroup
  name: 'westus'
  properties: {
    hostName: 'award-api-westus.<your-unique-suffix>.westus.azurecontainerapps.io'   // ← replace with actual hostname after first deploy
    httpPort: 80
    httpsPort: 443
    originHostHeader: 'award-api-westus.<your-unique-suffix>.westus.azurecontainerapps.io'
    priority: 1
    weight: 50
    enabledState: 'Enabled'
    enforceCertificateNameCheck: true
  }
}

resource endpoint 'Microsoft.Cdn/profiles/afdendpoints@2025-04-15' = {
  parent: frontDoorProfile
  name: 'award-nomination-api'
  location: location
  properties: {
    isHttpAllowed: false
    isHttpsAllowed: true
    autoPurgeContentOnEndpointCreate: true
  }
}

resource route 'Microsoft.Cdn/profiles/afdendpoints/routes@2025-04-15' = {
  parent: endpoint
  name: 'rt-award-api'
  properties: {
    originGroup: {
      id: originGroup.id
    }
    supportedProtocols: [
      'Https'
    ]
    patternsToMatch: [
      '/*'
    ]
    forwardingProtocol: 'MatchRequest'
    linkToDefaultDomain: 'Enabled'
    httpsRedirect: 'Enabled'
    enabledState: 'Enabled'
  }
}

output endpoint string = endpoint.properties.hostName
output profileId string = frontDoorProfile.id
