@description('The name of the AI Services account')
param accountName string

@description('The location for the AI Services account')
param location string

@description('The SKU name for the account')
param skuName string = 'S0'

@description('Enable public network access')
param publicNetworkAccess string = 'Enabled'

@description('Disable local authentication')
param disableLocalAuth bool = true

@description('Tags for the resources')
param tags object = {}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: toLower(accountName)
    networkAcls: {
      defaultAction: 'Allow'
      virtualNetworkRules: []
      ipRules: []
    }
    publicNetworkAccess: publicNetworkAccess
    disableLocalAuth: disableLocalAuth
  }
}

output id string = account.id
output name string = account.name
output endpoint string = account.properties.endpoint
output principalId string = account.identity.principalId
