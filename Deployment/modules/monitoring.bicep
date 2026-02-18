param locationEast string
param locationWest string

resource laWest 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: 'workspace-rgawardnomination57mY'
  location: locationWest
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource laEast 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: 'workspace-rgawardnomination6aem'
  location: locationEast
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// Your 2 custom saved searches only
resource getAwardApiLogs 'Microsoft.OperationalInsights/workspaces/savedSearches@2025-02-01' = {
  parent: laEast
  name: '1b0c9162-364a-493e-96a4-038973c200de_getawardapilogs'
  properties: {
    category: 'function'
    displayName: 'GetAwardAPILogs'
    version: 2
    functionAlias: 'GetAwardAPILogs'
    query: '// Save this as a function named "GetAwardAPILogs"\r\n.create function GetAwardAPILogs(hoursAgo:int) {\r\n    ContainerAppConsoleLogs_CL\r\n    | where TimeGenerated > ago(hoursAgo * 1h)\r\n    | where ContainerAppName_s in ("award-api-eastus", "award-api-westus")\r\n    | extend LogData = parse_json(Log_s)\r\n    | where isnotempty(LogData.level)\r\n    | extend \r\n        PacificTime = datetime_utc_to_local(TimeGenerated, \'US/Pacific\'),\r\n        LogLevel = tostring(LogData.level),\r\n        Logger = tostring(LogData.logger),\r\n        Message = tostring(LogData.message),\r\n        NominationId = toint(LogData.nomination_id),\r\n        UserId = toint(LogData.user_id)\r\n    | project PacificTime, LogLevel, Logger, Message, NominationId, UserId, Region = ContainerAppName_s\r\n}'
  }
}

resource getAwardNominationLogs 'Microsoft.OperationalInsights/workspaces/savedSearches@2025-02-01' = {
  parent: laEast
  name: 'dc87abb7-5e8a-4354-8020-a6ff546e905a_getawardnominationlogs'
  properties: {
    category: 'AwardNomination'
    displayName: 'GetAwardNominationLogs'
    version: 2
    functionAlias: 'GetAwardNominationLogs'
    query: 'create-or-alter function GetAwardNominationLogs(ModuleParam:string, LogLevelParam:string, HoursAgo:int) {\r\n    ContainerAppConsoleLogs_CL\r\n    | where TimeGenerated > ago(HoursAgo * 1h)\r\n    | where ContainerAppName_s in ("award-api-eastus", "award-api-westus")\r\n    | extend LogData = parse_json(Log_s)\r\n    | extend \r\n        PacificTime = datetime_utc_to_local(TimeGenerated, \'US/Pacific\'),\r\n        Message = tostring(LogData.message),\r\n        Module = tostring(LogData.logger),\r\n        LogLevel = tostring(LogData.level),\r\n        Exception = tostring(LogData.exception),\r\n        NominationId = toint(LogData.nomination_id),\r\n        UserId = toint(LogData.user_id)\r\n    | where Module == ModuleParam and LogLevel == LogLevelParam\r\n    | project PacificTime, LogLevel, Message, Module, Exception, NominationId, UserId, Region = ContainerAppName_s\r\n    | order by PacificTime desc\r\n}'
  }
}

output laWestId string = laWest.id
output laEastId string = laEast.id
