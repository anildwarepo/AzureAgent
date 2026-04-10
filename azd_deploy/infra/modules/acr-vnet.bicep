@description('The location for all resources')
param location string

@description('The name of the Virtual Network')
param vnetName string = 'vnet-acr'

@description('The address prefix for the Virtual Network')
param vnetAddressPrefix string = '10.0.0.0/16'

@description('The name of the default subnet')
param defaultSubnetName string = 'default'

@description('The address prefix for the default subnet')
param defaultSubnetAddressPrefix string = '10.0.0.0/24'

@description('The name of the private endpoint subnet')
param privateEndpointSubnetName string = 'private-endpoints'

@description('The address prefix for the private endpoint subnet')
param privateEndpointSubnetAddressPrefix string = '10.0.1.0/24'

@description('The name of the Container Apps subnet')
param containerAppsSubnetName string = 'container-apps'

@description('The address prefix for the Container Apps subnet')
param containerAppsSubnetAddressPrefix string = '10.0.2.0/23'

@description('The name of the Azure Container Registry')
param acrName string

@description('Enable ACR build tasks (requires public network access)')
param enableAcrBuildTasks bool = true

@description('Tags for all resources')
param tags object = {}

// Virtual Network Module
module vnet 'vnet.bicep' = {
  name: 'vnet-deployment'
  params: {
    location: location
    vnetName: vnetName
    vnetAddressPrefix: vnetAddressPrefix
    subnets: [
      {
        name: defaultSubnetName
        addressPrefix: defaultSubnetAddressPrefix
      }
      {
        name: privateEndpointSubnetName
        addressPrefix: privateEndpointSubnetAddressPrefix
        privateEndpointNetworkPolicies: 'Disabled'
      }
      {
        name: containerAppsSubnetName
        addressPrefix: containerAppsSubnetAddressPrefix
        delegation: 'Microsoft.App/environments'
      }
    ]
    tags: tags
  }
}

// Azure Container Registry Module
module acr 'acr.bicep' = {
  name: 'acr-deployment'
  params: {
    location: location
    acrName: acrName
    adminUserEnabled: false
    publicNetworkAccess: enableAcrBuildTasks ? 'Enabled' : 'Disabled'
    networkRuleBypassOptions: 'AzureServices'
    zoneRedundancy: 'Disabled'
    tags: tags
  }
}

// Private Endpoint Module for ACR
module acrPrivateEndpoint 'private-endpoint.bicep' = {
  name: 'acr-private-endpoint-deployment'
  params: {
    location: location
    privateEndpointName: '${acrName}-pe'
    subnetId: vnet.outputs.subnets[1].id
    privateLinkServiceId: acr.outputs.id
    groupIds: ['registry']
    privateDnsZoneName: 'privatelink.azurecr.io'
    vnetId: vnet.outputs.id
    vnetName: vnet.outputs.name
    tags: tags
  }
}

// Outputs
output vnetId string = vnet.outputs.id
output vnetName string = vnet.outputs.name
output defaultSubnetId string = vnet.outputs.subnets[0].id
output privateEndpointSubnetId string = vnet.outputs.subnets[1].id
output containerAppsSubnetId string = vnet.outputs.subnets[2].id
output acrId string = acr.outputs.id
output acrName string = acr.outputs.name
output acrLoginServer string = acr.outputs.loginServer
output privateEndpointId string = acrPrivateEndpoint.outputs.privateEndpointId
