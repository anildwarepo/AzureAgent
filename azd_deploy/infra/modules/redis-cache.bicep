@description('The name of the Azure Cache for Redis instance')
param redisName string

@description('The location for the Redis instance')
param location string

@description('The SKU name for Redis (Basic, Standard, Premium)')
@allowed(['Basic', 'Standard', 'Premium'])
param skuName string = 'Basic'

@description('The SKU family (C for Basic/Standard, P for Premium)')
@allowed(['C', 'P'])
param skuFamily string = 'C'

@description('The size of the Redis cache (0-6 for C family, 1-5 for P family)')
param skuCapacity int = 0

@description('Enable non-SSL port (6379). SSL port 6380 is always enabled.')
param enableNonSslPort bool = false

@description('Minimum TLS version')
param minimumTlsVersion string = '1.2'

@description('Tags for the resources')
param tags object = {}

resource redis 'Microsoft.Cache/redis@2024-03-01' = {
  name: redisName
  location: location
  tags: tags
  properties: {
    sku: {
      name: skuName
      family: skuFamily
      capacity: skuCapacity
    }
    enableNonSslPort: enableNonSslPort
    minimumTlsVersion: minimumTlsVersion
    publicNetworkAccess: 'Enabled'
    redisConfiguration: {
      'maxmemory-policy': 'noeviction'
    }
  }
}

@description('The resource ID of the Redis instance')
output id string = redis.id

@description('The hostname of the Redis instance')
output hostName string = redis.properties.hostName

@description('The SSL port of the Redis instance')
output sslPort int = redis.properties.sslPort

@description('The name of the Redis instance')
output name string = redis.name
