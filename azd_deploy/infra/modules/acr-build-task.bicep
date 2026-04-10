@description('The name of the Azure Container Registry')
param acrName string

@description('The location for the resources')
param location string

@description('The name of the image to build')
param imageName string

@description('The tag for the image')
param imageTag string = 'latest'

@description('The source location (Git URL or context path)')
param sourceLocation string

@description('The path to the Dockerfile relative to the source')
param dockerfilePath string = 'Dockerfile'

@description('Build arguments as key-value pairs')
param buildArgs object = {}

@description('Force a new build by changing this value - must be provided by caller')
param forceUpdateTag string

@description('Tags for the resources (reserved for future use)')
#disable-next-line no-unused-params
param tags object = {}

// Reference existing ACR
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

// Build arguments array
var buildArgsArray = [for item in items(buildArgs): {
  name: item.key
  value: item.value
}]

// ACR Task Run for building the image
resource acrBuildTask 'Microsoft.ContainerRegistry/registries/taskRuns@2019-06-01-preview' = {
  parent: acr
  name: 'build-${imageName}-${uniqueString(forceUpdateTag)}'
  location: location
  properties: {
    forceUpdateTag: forceUpdateTag
    runRequest: {
      type: 'DockerBuildRequest'
      dockerFilePath: dockerfilePath
      imageNames: [
        '${imageName}:${imageTag}'
        '${imageName}:${uniqueString(forceUpdateTag)}'
      ]
      isPushEnabled: true
      sourceLocation: sourceLocation
      platform: {
        os: 'Linux'
        architecture: 'amd64'
      }
      arguments: buildArgsArray
      timeout: 3600
      agentConfiguration: {
        cpu: 2
      }
    }
  }
}

output taskRunId string = acrBuildTask.id
output taskRunName string = acrBuildTask.name
output imageRepository string = imageName
output imageTag string = imageTag
output fullImageName string = '${acr.properties.loginServer}/${imageName}:${imageTag}'
