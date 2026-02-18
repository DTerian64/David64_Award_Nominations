param locationEast string
param locationWest string
param acrName string
param kvName string

resource acr 'Microsoft.ContainerRegistry/registries@2025-05-01-preview' = {
  name: acrName
  location: 'westus2'
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

resource caeEast 'Microsoft.App/managedEnvironments@2025-02-02-preview' = {
  name: 'cae-award-eastus'
  location: locationEast
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: '384196bb-882f-460e-89f6-9b7e160d6187' }  // update after deploy if needed
    }
    publicNetworkAccess: 'Enabled'
  }
}

resource caeWest 'Microsoft.App/managedEnvironments@2025-02-02-preview' = {
  name: 'cae-award-westus'
  location: locationWest
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: '290020d5-7bb8-4faa-a901-b5da4ad250d7' }
    }
    publicNetworkAccess: 'Enabled'
  }
}

// Container Apps (cleaned – image will be updated after deployment to new ACR)
resource apiEast 'Microsoft.App/containerapps@2025-02-02-preview' = {
  name: 'award-api-eastus'
  location: locationEast
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: caeEast.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'Auto' }
      registries: [{ server: '${acrName}.azurecr.io', username: acrName, passwordSecretRef: 'acr-password' }]
      secrets: [ /* your secret refs – re-create in new KV after deploy */ ]
    }
    template: { /* your containers block – keep as-is but update image later */ }
  }
}

resource apiWest 'Microsoft.App/containerapps@2025-02-02-preview' = { /* same pattern as east */ }

// Add outputs for loginServer, etc.
output acrLoginServer string = acr.properties.loginServer
