@description('The name of the parent AI Services account')
param accountName string

@description('The name of the model deployment')
param deploymentName string

@description('The name of the model')
param modelName string

@description('The format of the model (e.g., OpenAI)')
param modelFormat string = 'OpenAI'

@description('The version of the model')
param modelVersion string

@description('The SKU name for the deployment')
param skuName string = 'GlobalStandard'

@description('The capacity in TPM')
param capacity int = 500000 

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: deploymentName
  sku: {
    capacity: capacity
    name: skuName
  }
  properties: {
    model: {
      name: modelName
      format: modelFormat
      version: modelVersion
    }
  }
}

output id string = modelDeployment.id
output name string = modelDeployment.name
