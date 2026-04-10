@description('The location for the Container Apps Environment')
param location string

@description('The name of the Container Apps Environment')
param containerAppsEnvironmentName string

@description('The resource ID of the subnet for the Container Apps Environment')
param subnetId string

@description('Enable internal only (no public endpoint)')
param internalOnly bool = true

@description('The name of the Log Analytics workspace')
param logAnalyticsWorkspaceName string = ''

@description('Tags for the resources')
param tags object = {}

// Log Analytics Workspace for Container Apps
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: !empty(logAnalyticsWorkspaceName) ? logAnalyticsWorkspaceName : '${containerAppsEnvironmentName}-logs'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// Container Apps Environment
resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: subnetId
      internal: internalOnly
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

output id string = containerAppsEnvironment.id
output name string = containerAppsEnvironment.name
output defaultDomain string = containerAppsEnvironment.properties.defaultDomain
output staticIp string = containerAppsEnvironment.properties.staticIp
output logAnalyticsWorkspaceId string = logAnalytics.id
