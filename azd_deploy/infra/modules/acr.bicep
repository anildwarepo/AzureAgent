@description('The location for the Azure Container Registry')
param location string

@description('The name of the Azure Container Registry')
param acrName string

@description('Enable admin user for the registry')
param adminUserEnabled bool = false

@description('Allow public network access')
param publicNetworkAccess string = 'Disabled'

@description('Network rule bypass options')
param networkRuleBypassOptions string = 'AzureServices'

@description('Enable zone redundancy')
param zoneRedundancy string = 'Disabled'

@description('Tags for the resources')
param tags object = {}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Premium'
  }
  properties: {
    adminUserEnabled: adminUserEnabled
    publicNetworkAccess: publicNetworkAccess
    networkRuleBypassOptions: networkRuleBypassOptions
    zoneRedundancy: zoneRedundancy
  }
}

output id string = acr.id
output name string = acr.name
output loginServer string = acr.properties.loginServer
