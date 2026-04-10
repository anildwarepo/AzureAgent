@description('The location for the Container App')
param location string

@description('The name of the Container App')
param containerAppName string

@description('The resource ID of the Container Apps Environment')
param containerAppsEnvironmentId string

@description('The container image to deploy')
param containerImage string

@description('The name of the Azure Container Registry')
param acrName string

@description('The target port for the container')
param targetPort int = 3002

@description('Enable external ingress')
param externalIngress bool = false

@description('CPU cores for the container')
param cpu string = '0.5'

@description('Memory for the container')
param memory string = '1Gi'

@description('Minimum replicas')
param minReplicas int = 1

@description('Maximum replicas')
param maxReplicas int = 3

@description('Environment variables for the container')
param environmentVariables array = []

@description('Secrets for the container (array of {name, value} objects)')
param secrets array = []

@description('Allow insecure (HTTP) ingress traffic without redirect to HTTPS')
param allowInsecure bool = false

@description('Tags for the resources')
param tags object = {}

// Reference existing ACR for managed identity access
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

// User-assigned managed identity for ACR pull
resource acrPullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${containerAppName}-identity'
  location: location
  tags: tags
}

// Role assignment for ACR pull - created BEFORE container app
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, acrPullIdentity.id, 'acrpull')
  scope: acr
  properties: {
    principalId: acrPullIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull
  }
}

// Container App with user-assigned managed identity
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${acrPullIdentity.id}': {}
    }
  }
  dependsOn: [
    acrPullRole // Ensure role assignment is complete before container app is created
  ]
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: externalIngress
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: allowInsecure
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: acrPullIdentity.id
        }
      ]
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: containerAppName
          image: containerImage
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          // Add AZURE_CLIENT_ID for managed identity, then append user-provided env vars
          env: concat([
            {
              name: 'AZURE_CLIENT_ID'
              value: acrPullIdentity.properties.clientId
            }
          ], environmentVariables)
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output id string = containerApp.id
output name string = containerApp.name
output fqdn string = containerApp.properties.configuration.ingress.fqdn
output identityId string = acrPullIdentity.id
output identityPrincipalId string = acrPullIdentity.properties.principalId
output latestRevisionName string = containerApp.properties.latestRevisionName
