param dnsZoneName string
param domainName string

// contactAdmin
@secure()
param email string
@secure()
param nameFirst string
@secure()
param nameLast string
@secure()
param phone string

// contactBilling
@secure()
param email_1 string
@secure()
param nameFirst_1 string
@secure()
param nameLast_1 string
@secure()
param phone_1 string

// contactRegistrant
@secure()
param email_2 string
@secure()
param nameFirst_2 string
@secure()
param nameLast_2 string
@secure()
param phone_2 string

// contactTech
@secure()
param email_3 string
@secure()
param nameFirst_3 string
@secure()
param nameLast_3 string
@secure()
param phone_3 string

// ── DNS Zone ──────────────────────────────────────────────────────────────────
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
    CNAMERecord: {
      cname: 'award-nomination-frontend.azurestaticapps.net'  // update after first frontend deploy if hostname differs
    }
  }
}

// ── Domain Registration ───────────────────────────────────────────────────────
resource appServiceDomain 'Microsoft.DomainRegistration/domains@2024-11-01' = {
  name: domainName
  location: 'global'
  properties: {
    privacy: true
    autoRenew: false
    dnsType: 'AzureDns'
    dnsZoneId: dnsZone.id
    contactAdmin: {
      email: email
      nameFirst: nameFirst
      nameLast: nameLast
      phone: phone
    }
    contactBilling: {
      email: email_1
      nameFirst: nameFirst_1
      nameLast: nameLast_1
      phone: phone_1
    }
    contactRegistrant: {
      email: email_2
      nameFirst: nameFirst_2
      nameLast: nameLast_2
      phone: phone_2
    }
    contactTech: {
      email: email_3
      nameFirst: nameFirst_3
      nameLast: nameLast_3
      phone: phone_3
    }
    consent: {}
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output dnsZoneId string = dnsZone.id
output nameServers array = dnsZone.properties.nameServers
