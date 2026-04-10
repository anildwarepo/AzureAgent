@description('The name of the parent AI Services account')
param accountName string

@description('The name of the project')
param projectName string

@description('The location for the project')
param location string

@description('The description of the project')
param projectDescription string = ''

@description('The display name of the project')
param projectDisplayName string = ''

@description('Tags for the resources')
param tags object = {}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: projectDescription
    displayName: projectDisplayName
  }
}

output id string = project.id
output name string = project.name
output principalId string = project.identity.principalId
