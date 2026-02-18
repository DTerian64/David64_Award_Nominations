param dnsZoneName string
param domainName string
@secure() param email string
@secure() param nameFirst string
@secure() param nameLast string
@secure() param phone string
// add the other 8 contact params the same way...

resource dnsZone 'Microsoft.Network/dnszones@2023-07-01-preview' = {
  name: dnsZoneName
  location: 'global'
  properties: { zoneType: 'Public' }
}

resource nsRecord 'Microsoft.Network/dnszones/NS@2023-07-01-preview' = {
  parent: dnsZone
  name: '@'
  properties: {
    TTL: 172800
    NSRecords: [
      { nsdname: 'ns1-04.azure-dns.com.' }
      { nsdname: 'ns2-04.azure-dns.net.' }
      { nsdname: 'ns3-04.azure-dns.org.' }
      { nsdname: 'ns4-04.azure-dns.info.' }
    ]
  }
}

resource soaRecord 'Microsoft.Network/dnszones/SOA@2023-07-01-preview' = {
  parent: dnsZone
  name: '@'
  properties: {
    TTL: 3600
    SOARecord: {
      email: 'azuredns-hostmaster.microsoft.com'
      expireTime: 2419200
      host: 'ns1-04.azure-dns.com.'
      minimumTTL: 300
      refreshTime: 3600
      retryTime: 300
      serialNumber: 1
    }
  }
}

resource txtRecord 'Microsoft.Network/dnszones/TXT@2023-07-01-preview' = {
  parent: dnsZone
  name: '@'
  properties: {
    TTL: 12960000
    TXTRecords: [{ value: ['MS=ms68100231'] }]
  }
}

resource cnameAwards 'Microsoft.Network/dnszones/CNAME@2023-07-01-preview' = {
  parent: dnsZone
  name: 'awards'
  properties: {
    TTL: 3600
    targetResource: { id: /* will be wired from frontend module output later */ }
  }
}

resource appServiceDomain 'Microsoft.DomainRegistration/domains@2024-11-01' = {
  name: domainName
  location: 'global'
  properties: {
    privacy: true
    autoRenew: false
    dnsType: 'AzureDns'
    dnsZoneId: dnsZone.id
    contactAdmin: { email: email, nameFirst: nameFirst, nameLast: nameLast, phone: phone }
    // repeat for billing, registrant, tech using the other params
  }
}
