param locationEast string
param locationWest string
param acrName string
param kvName string
param laEastCustomerId string   // passed in from monitoring module output
param laWestCustomerId string   // passed in from monitoring module output
param laEastResourceId string   // passed in from monitoring module output — needed for listKeys()
param laWestResourceId string   // passed in from monitoring module output — needed for listKeys()
param frontDoorEndpoint string  // passed in from frontDoor module output on second+ deploys
@secure()
param emailActionSecretKey string

// ── ACR ──────────────────────────────────────────────────────────────────────
resource acr 'Microsoft.ContainerRegistry/registries@2025-05-01-preview' = {
  name: acrName
  location: locationWest
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

// ── Managed Environments ─────────────────────────────────────────────────────
resource caeEast 'Microsoft.App/managedEnvironments@2025-02-02-preview' = {
  name: 'cae-award-eastus'
  location: locationEast
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: laEastCustomerId
        sharedKey: listKeys(laEastResourceId, '2025-02-01').primarySharedKey
      }
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
      logAnalyticsConfiguration: {
        customerId: laWestCustomerId
        sharedKey: listKeys(laWestResourceId, '2025-02-01').primarySharedKey
      }
    }
    publicNetworkAccess: 'Enabled'
  }
}

// ── Shared secret + env var definitions ──────────────────────────────────────
// Plain secrets: value is '' here and must be set post-deploy via pipeline
// (az containerapp secret set ...) — empty string is a valid placeholder for Bicep
var plainSecrets = [
  { name: 'gmail-app-password',                          value: '' }
  { name: 'sql-database',                                value: '' }
  { name: 'sql-password',                                value: '' }
  { name: 'sql-server',                                  value: '' }
  { name: 'sql-user',                                    value: '' }
  { name: 'acrawardnominationazurecrio-acrawardnomination', value: '' }
]

// KV-referenced secret: pulled automatically via the container app's system-assigned identity
var kvSecrets = [
  {
    name: 'azure-storage-key'
    keyVaultUrl: 'https://${kvName}.vault.azure.net/secrets/AZURE-STORAGE-KEY'
    identity: 'system'
  }
]

var commonSecrets = concat(plainSecrets, kvSecrets)

var commonEnvVars = [
  { name: 'SQL_SERVER',          secretRef: 'sql-server' }
  { name: 'SQL_DATABASE',        secretRef: 'sql-database' }
  { name: 'SQL_USER',            secretRef: 'sql-user' }
  { name: 'SQL_PASSWORD',        secretRef: 'sql-password' }
  { name: 'AZURE_STORAGE_ACCOUNT', value: 'awardnominationmodels' }
  { name: 'MODEL_CONTAINER',     value: 'ml-models' }
  { name: 'MODEL_BLOB_NAME',     value: 'fraud_detection_model.pkl' }
  { name: 'ENVIRONMENT',         value: 'production' }
  { name: 'AZURE_STORAGE_KEY',   secretRef: 'azure-storage-key' }
  { name: 'GMAIL_APP_PASSWORD',  secretRef: 'gmail-app-password' }
  { name: 'GMAIL_USER',          value: 'david.terian@gmail.com' }
  { name: 'FROM_EMAIL',          value: 'noreply@terian-services.com' }
  { name: 'FROM_NAME',           value: 'Award Nomination System' }
  { name: 'API_BASE_URL',        value: 'https://${frontDoorEndpoint}' }
  { name: 'EMAIL_ACTION_SECRET_KEY',    value: emailActionSecretKey }
  { name: 'EMAIL_ACTION_TOKEN_EXPIRY_HOURS', value: '72' }
]

var commonProbes = [
  {
    type: 'Liveness'
    failureThreshold: 3
    periodSeconds: 10
    successThreshold: 1
    tcpSocket: { port: 8000 }
    timeoutSeconds: 5
  }
  {
    type: 'Readiness'
    failureThreshold: 48
    periodSeconds: 5
    successThreshold: 1
    tcpSocket: { port: 8000 }
    timeoutSeconds: 5
  }
  {
    type: 'Startup'
    failureThreshold: 240
    initialDelaySeconds: 1
    periodSeconds: 1
    successThreshold: 1
    tcpSocket: { port: 8000 }
    timeoutSeconds: 3
  }
]

// ── Container App – East US ───────────────────────────────────────────────────
resource apiEast 'Microsoft.App/containerapps@2025-02-02-preview' = {
  name: 'award-api-eastus'
  location: locationEast
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: caeEast.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 100
      ingress: {
        external: true
        targetPort: 8000
        exposedPort: 0
        transport: 'Auto'
        traffic: [{ weight: 100, latestRevision: true }]
        allowInsecure: false
      }
      registries: [{
        server: '${acrName}.azurecr.io'
        username: acrName
        passwordSecretRef: 'acrawardnominationazurecrio-acrawardnomination'
      }]
      secrets: commonSecrets
      identitySettings: []
    }
    template: {
      containers: [{
        name: 'award-api-eastus'
        image: '${acrName}.azurecr.io/award-nomination-api:latest'  // tag updated by CI/CD pipeline
        resources: { cpu: '1', memory: '2Gi' }  // Increased from 0.5 CPU/1Gi for fraud detection workload
        env: concat(commonEnvVars, [
          { name: 'REGION',              value: 'eastus' }
          { name: 'CONTAINER_APP_NAME',  value: 'award-api-eastus' }
        ])
        probes: commonProbes
      }]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        cooldownPeriod: 300
        pollingInterval: 30
      }
      volumes: []
    }
  }
}

// ── Container App – West US ───────────────────────────────────────────────────
resource apiWest 'Microsoft.App/containerapps@2025-02-02-preview' = {
  name: 'award-api-westus'
  location: locationWest
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: caeWest.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 100
      ingress: {
        external: true
        targetPort: 8000
        exposedPort: 0
        transport: 'Auto'
        traffic: [{ weight: 100, latestRevision: true }]
        allowInsecure: false
      }
      registries: [{
        server: '${acrName}.azurecr.io'
        username: acrName
        passwordSecretRef: 'acrawardnominationazurecrio-acrawardnomination'
      }]
      secrets: commonSecrets
      identitySettings: []
    }
    template: {
      containers: [{
        name: 'award-api-westus'
        image: '${acrName}.azurecr.io/award-nomination-api:latest'
        resources: { cpu: '1', memory: '2Gi' }  // Increased from 0.5 CPU/1Gi for fraud detection workload
        env: concat(commonEnvVars, [
          { name: 'REGION',              value: 'westus' }
          { name: 'CONTAINER_APP_NAME',  value: 'award-api-westus' }
        ])
        probes: commonProbes
      }]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        cooldownPeriod: 300
        pollingInterval: 30
      }
      volumes: []
    }
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output acrLoginServer string = acr.properties.loginServer
output apiEastFqdn string = apiEast.properties.configuration.ingress.fqdn
output apiWestFqdn string = apiWest.properties.configuration.ingress.fqdn
