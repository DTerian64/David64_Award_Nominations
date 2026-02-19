using './main.bicep'

// ── Locations ─────────────────────────────────────────────────────────────────
param locationEast = 'eastus'
param locationWest = 'westus2'

// ── SQL ───────────────────────────────────────────────────────────────────────
param sqlAdminLogin = 'dterian'
param sqlAdminPassword = getSecret('4ddf12bb-5397-445f-bcaa-df4c7d3dfdca', 'rg_award_nomination', 'kv-awardnominations', 'sql-admin-password')

// ── Resource names ────────────────────────────────────────────────────────────
param acrName = 'acrawardnomination'
param kvName = 'kv-awardnominations'
param sqlServerName = 'david64-sql'
param storageName = 'awardnominationmodels'
param dnsZoneName = 'terian-services.com'
param staticSiteName = 'award-nomination-frontend'
param frontDoorName = 'Award-Nomination-ADF'

// ── Front Door endpoint (empty on first deploy) ───────────────────────────────
param frontDoorEndpoint = ''

// ── Email action secret ───────────────────────────────────────────────────────
param emailActionSecretKey = getSecret('4ddf12bb-5397-445f-bcaa-df4c7d3dfdca', 'rg_award_nomination', 'kv-awardnominations', 'email-action-secret-key')

// ── Domain registration contacts ──────────────────────────────────────────────
// NOTE: keep this file in .gitignore — contact details are @secure() params
// Phone must be E.164 format: +countrycode.number
param domains_terian_services_com_email      = 'david.terian@gmail.com'
param domains_terian_services_com_nameFirst  = 'David'
param domains_terian_services_com_nameLast   = 'Terian'
param domains_terian_services_com_phone      = '+1.8479710946'

param domains_terian_services_com_email_1    = 'david.terian@gmail.com'
param domains_terian_services_com_nameFirst_1 = 'David'
param domains_terian_services_com_nameLast_1  = 'Terian'
param domains_terian_services_com_phone_1     = '+1.8479710946'

param domains_terian_services_com_email_2    = 'david.terian@gmail.com'
param domains_terian_services_com_nameFirst_2 = 'David'
param domains_terian_services_com_nameLast_2  = 'Terian'
param domains_terian_services_com_phone_2     = '+1.8479710946'

param domains_terian_services_com_email_3    = 'david.terian@gmail.com'
param domains_terian_services_com_nameFirst_3 = 'David'
param domains_terian_services_com_nameLast_3  = 'Terian'
param domains_terian_services_com_phone_3     = '+1.8479710946'
