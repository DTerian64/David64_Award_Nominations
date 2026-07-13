workspace "Award Nomination App" "Structured architecture and workflow model for the Award Nomination App documentation." {

    model {
        employee = person "Employee / Manager / HRBP / Admin" "Tenant users who submit nominations, approve awards, review flagged items, administer tenants, or inspect analytics."
        payrollBp = person "Payroll BP" "Tenant user with PayrollBP role who can inspect payroll data and support payout reconciliation."
        platformOperator = person "Platform Operator" "Engineer or support operator responsible for deployment, observability, and incident response."

        award = softwareSystem "Award Nomination App" "Tenant-configurable SaaS platform for employee award nomination, integrity review, analytics, and payroll coordination." {
            frontend = container "React Frontend" "User experience for nominations, approvals, HRBP review, analytics, payroll lookup, tenant theming, localization, and impersonation." "React, TypeScript, Vite, MSAL, i18next"
            api = container "Backend API" "Tenant-aware API for authentication, nomination lifecycle, analytics, admin operations, certificates, payroll lookup, and event publishing." "FastAPI, Python, SQLAlchemy, pyodbc"
            integrityWorker = container "Integrity Check Worker" "Consumes nomination.submitted, runs description checks and fraud scoring, and routes nominations to manager approval, HRBP review, or rejection." "Python, Azure Service Bus SDK, scikit-learn, sentence-transformers, SHAP"
            auxiliaryWorker = container "Auxiliary Worker" "Consumes downstream award events, sends notifications, handles HRBP alerts, payout events, payroll outcomes, and idempotent side effects." "Python, Azure Service Bus SDK, SMTP"
            payrollBroker = container "Payroll Broker" "Provider abstraction for payroll lookup, OAuth/webhooks, and approved award payout submission." "FastAPI, Python, provider adapters"
            analyticsJob = container "Fraud Analytics Job" "Scheduled analytics job for graph-pattern detection, fraud model retraining, holiday sync, and forecast model generation." "Python, Container Apps Job, pandas, scikit-learn, networkx"
            mcpServices = container "MCP / Analytics Export Services" "SQL and export services used by analytics and investigation workflows." "Python, MCP"
            schemaMigrationJob = container "Schema Migration Job" "In-VNet Container Apps Job that applies Alembic migrations to Azure SQL as the sql-migrations managed identity (db_ddladmin). Triggered by GitHub Actions over ARM." "Python, Alembic, Azure Container Apps Job"
            database = container "Azure SQL Database" "System of record for tenants, users, nominations, fraud scores, review state, analytics, conversations, event processing, and payroll metadata." "Azure SQL" {
                tenants = component "Tenants" "Tenant registry, Entra tenant mapping, domain, site URL, branding, and tenant configuration." "Table" "Auditable"
                users = component "Users" "Tenant-scoped user roster, UPN/email, profile fields, and manager hierarchy." "Table" "Auditable"
                userRoles = component "UserRoles" "Application roles such as HRBP, PayrollBP, and Support." "Table" "Auditable"
                nominations = component "Nominations" "Award workflow records and lifecycle status." "Table" "Auditable"
                nominationCategories = component "NominationCategories" "Tenant-specific award categories." "Table" "Auditable"
                emailTemplates = component "EmailTemplates" "Tenant-editable notification email templates (subject, body, version)." "Table" "Auditable"
                processedEvents = component "ProcessedEvents" "Service Bus idempotency log and processing result table." "Table"
                fraudScores = component "Fraud Score Tables" "P2P, approver, and HRBP fraud score and feature-summary tables." "Tables"
                graphFindings = component "Graph and Integrity Findings" "Graph pattern findings and point-in-time graph flag snapshots." "Tables"
                forecastTables = component "Forecast Tables" "Forecast run metadata and forecast points." "Tables"
                conversations = component "Ask Conversations and Messages" "AI analytics conversation headers, messages, and export metadata." "Tables"
                payrollProviders = component "payroll_providers" "Per-tenant payroll provider configuration and external company id." "Table" "Auditable"
                payrollTokens = component "payroll_tokens" "Rotating OAuth credentials (AES-256-GCM ciphertext) per provider." "Table" "Auditable"
                payrollSubmissions = component "payroll_submissions" "One row per payout submission; bridges the provider payroll ref back to the nomination." "Table" "Auditable"
                auditTables = component "Audit and Demo Tables" "Impersonation audit logs and demo registration requests." "Tables"

                tenants -> users "Owns"
                tenants -> nominationCategories "Configures"
                tenants -> emailTemplates "Configures"
                tenants -> payrollProviders "Configures payroll via"
                tenants -> graphFindings "Produces"
                tenants -> forecastTables "Produces"
                tenants -> conversations "Owns"
                users -> nominations "Nominates, receives, and approves"
                nominations -> fraudScores "Has"
                nominations -> payrollSubmissions "Pays through"
                payrollProviders -> payrollTokens "Authenticated by"
                payrollProviders -> payrollSubmissions "Executes payouts recorded in"
                nominations -> processedEvents "Referenced by"
                conversations -> auditTables "Audited separately from"
            }
            serviceBus = container "Azure Service Bus" "award-events topic with fraud-processor, email-processor, and payroll-processor subscriptions." "Azure Service Bus"
            blobStorage = container "Azure Blob Storage" "Stores fraud models, certificates, certificate templates, and exports." "Azure Blob Storage"
            openAiAdapter = container "Azure OpenAI Adapter" "Calls Azure OpenAI for semantic checks, explanations, ask analytics, and investigations." "OpenAI SDK"
        }

        entra = softwareSystem "Microsoft Entra ID" "Identity provider for user authentication and admin app roles." "External"
        frontDoor = softwareSystem "Azure Front Door + WAF" "Edge routing, WAF, health probes, TLS, and CORS handling." "External"
        staticWebApps = softwareSystem "Azure Static Web Apps" "Hosts the React frontend." "External"
        smtp = softwareSystem "SMTP / Email Provider" "Sends workflow, HRBP, demo, payroll, and analytics notification emails." "External"
        payrollProviders = softwareSystem "Payroll Providers" "Gusto, Rippling, and Workday-style providers for employee pay lookup and award payout execution." "External"
        appInsights = softwareSystem "Application Insights + Log Analytics" "Telemetry, traces, logs, dashboards, KQL validation, and operational analytics." "External"
        keyVault = softwareSystem "Azure Key Vault" "Stores SQL credentials, provider secrets, webhook secrets, OpenAI keys, and Application Insights connection strings." "External"
        containerApps = softwareSystem "Azure Container Apps" "Runtime hosting for API, workers, payroll broker, and scheduled jobs." "External"
        github = softwareSystem "GitHub Actions" "CI/CD pipeline on GitHub-hosted runners, outside the VNet; drives the ARM control plane only, never SQL." "External"
        acr = softwareSystem "Azure Container Registry" "Stores application and schema-migration images; pulled in-VNet over a private endpoint." "External"

        employee -> frontend "Uses"
        payrollBp -> frontend "Uses payroll lookup and award context"
        platformOperator -> appInsights "Monitors and investigates"

        frontend -> entra "Authenticates users with MSAL"
        frontend -> frontDoor "Calls API through"
        frontend -> api "Calls backend APIs"
        staticWebApps -> frontend "Hosts"
        frontDoor -> api "Routes API traffic to"
        frontDoor -> payrollBroker "Routes payroll OAuth and webhook traffic to"

        api -> entra "Validates JWT issuer, audience, and tenant"
        api -> database "Reads and writes tenant-scoped business data"
        api -> serviceBus "Publishes award lifecycle events"
        api -> blobStorage "Reads/writes certificates, exports, and model metadata"
        api -> openAiAdapter "Uses for analytics and explanations"
        api -> payrollBroker "Calls payroll lookup API"
        api -> appInsights "Emits logs, metrics, and traces"
        api -> keyVault "Reads secrets via managed identity"

        serviceBus -> integrityWorker "Delivers nomination.submitted"
        serviceBus -> auxiliaryWorker "Delivers downstream notification and outcome events"
        serviceBus -> payrollBroker "Delivers nomination.approved payout events"

        integrityWorker -> database "Reads nomination context and writes fraud flags/status"
        integrityWorker -> blobStorage "Streams tenant fraud model artifacts"
        integrityWorker -> openAiAdapter "Runs semantic checks and explanations"
        integrityWorker -> serviceBus "Publishes created, rejected, or flagged routing events"
        integrityWorker -> appInsights "Emits logs and traces"

        auxiliaryWorker -> database "Reads templates and updates workflow/payment state"
        auxiliaryWorker -> smtp "Sends notification emails"
        auxiliaryWorker -> serviceBus "Publishes follow-up events where required"
        auxiliaryWorker -> appInsights "Emits logs and traces"

        payrollBroker -> database "Reads provider configuration and records payroll submissions"
        payrollBroker -> payrollProviders "Submits payouts, performs employee lookup, and receives webhooks"
        payrollProviders -> payrollBroker "Returns provider API responses and webhook callbacks"
        payrollBroker -> serviceBus "Publishes payroll.accepted or payroll.failed"
        payrollBroker -> appInsights "Emits logs and traces"
        payrollBroker -> keyVault "Reads provider secrets and token encryption key"

        analyticsJob -> database "Reads nominations and writes graph, score, and forecast data"
        analyticsJob -> blobStorage "Uploads tenant model artifacts"
        analyticsJob -> api "Calls internal model refresh endpoint"
        analyticsJob -> appInsights "Emits job logs and metrics"

        mcpServices -> database "Reads tenant-scoped analytics data"
        mcpServices -> blobStorage "Writes export artifacts"

        github -> acr "Builds and pushes the schema-migration image"
        github -> containerApps "Runs az containerapp job update, start, and status over ARM"
        containerApps -> schemaMigrationJob "Launches a job execution inside the VNet"
        acr -> schemaMigrationJob "Provides the migration image via private endpoint"
        schemaMigrationJob -> entra "Obtains a managed-identity token for Azure SQL (sql-migrations, db_ddladmin)"
        schemaMigrationJob -> database "Applies alembic upgrade head over the private endpoint"
        schemaMigrationJob -> appInsights "Emits job logs and traces"

        deploymentEnvironment "Azure" {
            deploymentNode "Users and External Providers" "Browsers, identity provider, payroll providers, and email infrastructure." {
                infrastructureNode "Microsoft Entra ID"
                infrastructureNode "Payroll Providers"
                infrastructureNode "SMTP / Email Provider"
            }

            deploymentNode "Azure Edge and Frontend" "Public entry points and static frontend hosting." {
                infrastructureNode "Azure Front Door Standard + WAF"
                infrastructureNode "Azure Static Web Apps"
                containerInstance frontend
            }

            deploymentNode "Azure Container Apps Environment - Primary Region" "Primary application runtime." {
                containerInstance api
                containerInstance integrityWorker
                containerInstance auxiliaryWorker
                containerInstance payrollBroker
                containerInstance analyticsJob
                containerInstance mcpServices
                containerInstance schemaMigrationJob
            }

            deploymentNode "Azure Container Apps Environment - Secondary Region" "Secondary backend API runtime behind Front Door." {
                containerInstance api
            }

            deploymentNode "Azure Data and Messaging" "Persistent data, events, artifacts, secrets, and AI services." {
                containerInstance database
                containerInstance serviceBus
                containerInstance blobStorage
                containerInstance openAiAdapter
                infrastructureNode "Azure Key Vault"
            }

            deploymentNode "Azure Observability" "Telemetry and operational analytics." {
                infrastructureNode "Application Insights + Log Analytics"
            }
        }

    }

    views {
        systemContext award "SystemContext" "System context for users, identity, Azure services, payroll providers, and observability." {
            include *
            autolayout lr
        }

        container award "ContainerArchitecture" "Container architecture for the Award Nomination App." {
            include *
            autolayout lr
        }

        component database "ConceptualDataModel" "Conceptual data model represented as Azure SQL table groups." {
            include *
            autolayout lr
        }

        component database "AuditableTables" "SOC 2 auditable tables (ADR-0001 / migration 0034): the nine key tables carrying the audit quartet created_at, created_by, updated_at, updated_by. The *_by columns hold the effective user UPN for human writes, or a svc: service marker (e.g. svc:integrity-check) for autonomous service writes." {
            include tenants users userRoles nominations nominationCategories emailTemplates payrollProviders payrollTokens payrollSubmissions
            autolayout lr
        }

        dynamic award "NominationCleanApprovalFlow" "Clean nomination path from submission through manager approval and payroll outcome." {
            employee -> frontend "Submits nomination"
            frontend -> api "POST /api/nominations"
            api -> database "Validates tenant, user, amount, category, manager, and description structure"
            api -> database "Saves nomination as Submitted"
            api -> serviceBus "Publishes nomination.submitted"
            serviceBus -> integrityWorker "Delivers to fraud-processor subscription"
            integrityWorker -> database "Loads nomination context"
            integrityWorker -> blobStorage "Loads tenant fraud model when available"
            integrityWorker -> openAiAdapter "Runs semantic checks when configured"
            integrityWorker -> database "Sets status Pending"
            integrityWorker -> serviceBus "Publishes nomination.created"
            serviceBus -> auxiliaryWorker "Delivers to email-processor subscription"
            auxiliaryWorker -> smtp "Sends manager approval email"
            employee -> frontend "Manager approves nomination"
            frontend -> api "POST /api/nominations/approve"
            api -> database "Sets status Approved"
            api -> serviceBus "Publishes nomination.approved"
            serviceBus -> auxiliaryWorker "Sends outcome email and payout request"
            serviceBus -> payrollBroker "Delivers to payroll-processor subscription"
            payrollBroker -> payrollProviders "Submits payroll payout"
            payrollBroker -> serviceBus "Publishes payroll.accepted"
            auxiliaryWorker -> database "Marks nomination Paid"
            autolayout lr
        }

        dynamic award "NominationHRBPReviewFlow" "Flagged nomination path through HRBP review before manager approval." {
            employee -> frontend "Submits nomination"
            frontend -> api "POST /api/nominations"
            api -> database "Saves nomination as Submitted"
            api -> serviceBus "Publishes nomination.submitted"
            serviceBus -> integrityWorker "Delivers to fraud-processor subscription"
            integrityWorker -> database "Saves fraud score and feature summary"
            integrityWorker -> database "Sets status PendingHRBPReview"
            integrityWorker -> serviceBus "Publishes nomination.fraud-flagged"
            serviceBus -> auxiliaryWorker "Delivers HRBP notification event"
            auxiliaryWorker -> smtp "Emails HRBP reviewers"
            employee -> frontend "HRBP approves or rejects"
            frontend -> api "POST /api/hrbp/nominations/{id}/approve or reject"
            api -> database "Writes HRBP decision and training label"
            api -> serviceBus "Publishes HRBP outcome event"
            auxiliaryWorker -> smtp "Notifies nominator"
            api -> serviceBus "If approved, publishes nomination.created for manager approval"
            autolayout lr
        }

        dynamic award "ModelLifecycle" "Weekly integrity analytics and model retraining lifecycle." {
            analyticsJob -> database "Reads production nominations, fraud scores, HRBP labels, and graph history"
            analyticsJob -> database "Runs graph pattern detection and writes findings"
            analyticsJob -> database "Materializes user and approver graph flag snapshots"
            analyticsJob -> database "Trains tenant-specific Random Forest models"
            analyticsJob -> database "Upserts historical P2P and approver scores"
            analyticsJob -> blobStorage "Uploads fraud_detection_model_tenant_(TenantId).pkl"
            analyticsJob -> api "POST /api/internal/refresh-fraud-model"
            api -> blobStorage "Refreshes cached tenant model artifacts"
            integrityWorker -> blobStorage "Streams latest model for new submissions"
            autolayout lr
        }

        dynamic award "PayrollIntegrationFlow" "Approved nomination payout flow through the payroll broker and provider registry." {
            api -> serviceBus "Publishes nomination.approved"
            serviceBus -> payrollBroker "Delivers event to payroll-processor subscription"
            payrollBroker -> database "Resolves tenant payroll provider, tokens, and nomination details"
            payrollBroker -> payrollProviders "Routes payout through provider adapter"
            payrollProviders -> payrollBroker "Returns accepted, failed, or webhook-confirmed result"
            payrollBroker -> database "Records payroll submission state"
            payrollBroker -> serviceBus "Publishes payroll.accepted or payroll.failed"
            serviceBus -> auxiliaryWorker "Delivers payroll result event"
            auxiliaryWorker -> database "Marks nomination Paid or records failure workflow"
            auxiliaryWorker -> smtp "Notifies support users on failure"
            autolayout lr
        }

        dynamic award "SchemaMigrationFlow" "ADR-0001 schema migration (deploy-schema-migration.yaml): a GitHub-hosted runner drives only the ARM control plane, while the in-VNet ACA Job applies Alembic to the private Azure SQL as the sql-migrations managed identity (db_ddladmin)." {
            github -> acr "Builds and pushes the migration image (tags :latest and :sha)"
            github -> containerApps "az containerapp job update --image sha (points the job at the new image)"
            github -> containerApps "az containerapp job start (triggers a job execution)"
            containerApps -> schemaMigrationJob "Starts the execution inside the VNet"
            acr -> schemaMigrationJob "Pulls the migration image over the private endpoint"
            schemaMigrationJob -> entra "Acquires a managed-identity token (sql-migrations, db_ddladmin)"
            schemaMigrationJob -> database "Runs alembic upgrade head over the private endpoint"
            schemaMigrationJob -> appInsights "Emits job logs and traces"
            github -> containerApps "Polls job execution status until Succeeded or Failed"
            autolayout lr
        }

        deployment award "Azure" "AzureDeployment" "Azure deployment topology for frontend, edge, API, workers, data, secrets, and observability." {
            include *
            autolayout tb
        }

        styles {
            element "Person" {
                shape person
                background #08427b
                color #ffffff
            }
            element "Software System" {
                background #1168bd
                color #ffffff
            }
            element "Container" {
                background #438dd5
                color #ffffff
            }
            element "Component" {
                background #85bbf0
                color #000000
            }
            element "Auditable" {
                background #f2c14e
                color #000000
            }
            element "External" {
                background #999999
                color #ffffff
            }
            relationship "Relationship" {
                color #707070
            }
        }
    }
}
