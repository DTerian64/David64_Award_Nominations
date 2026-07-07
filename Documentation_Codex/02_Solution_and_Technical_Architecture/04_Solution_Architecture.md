# Solution Architecture

## Architecture Summary

The solution is an Azure-native SaaS application with:

- React/Vite frontend hosted by Azure Static Web Apps.
- FastAPI backend hosted on Azure Container Apps in primary and secondary regions.
- Azure Front Door Standard and WAF routing external traffic to backend origins and payroll broker origins.
- Azure SQL as the relational system of record.
- Azure Service Bus topic and subscriptions for asynchronous workflow events.
- Separate worker services for integrity checking, notifications, payout handling, and payroll processing.
- Azure Blob Storage for fraud models, certificates, templates, and exports.
- Azure OpenAI and local ML libraries for analytics, semantic checks, and investigation support.
- Application Insights and Log Analytics for observability.

## System Context

```mermaid
flowchart LR
    Employee["Employee / Manager / HRBP / Admin"] --> Frontend["React Frontend<br/>Azure Static Web Apps"]
    Frontend --> AFD["Azure Front Door + WAF"]
    AFD --> API["FastAPI Backend<br/>Azure Container Apps"]
    API --> SQL["Azure SQL"]
    API --> SB["Azure Service Bus<br/>award-events topic"]
    API --> Blob["Azure Blob Storage"]
    API --> OpenAI["Azure OpenAI"]
    API --> Entra["Microsoft Entra ID"]
    SB --> Integrity["Integrity Check Worker"]
    SB --> Auxiliary["Auxiliary Worker"]
    SB --> Payroll["Payroll Broker"]
    Integrity --> SQL
    Integrity --> Blob
    Integrity --> OpenAI
    Integrity --> SB
    Auxiliary --> SQL
    Auxiliary --> Email["SMTP / Email Provider"]
    Auxiliary --> SB
    Payroll --> Providers["Gusto / Rippling / Workday Pattern"]
    Payroll --> SQL
    Payroll --> SB
    Job["Fraud Analytics Job"] --> SQL
    Job --> Blob
    Job --> API
```

## Major Containers

| Container | Path | Primary responsibility |
| --- | --- | --- |
| Frontend | `frontend` | User experience, MSAL sign-in, tenant theming, nomination UI, approvals, analytics, HRBP, payroll lookup. |
| Backend API | `backend` | Authentication, tenant resolution, nomination APIs, analytics APIs, admin functions, certificate SAS links, Service Bus publishing. |
| Integrity Check Worker | `integrity-check` | Consumes `nomination.submitted`, runs description checks and fraud assessment, routes clean/flagged/rejected outcomes. |
| Auxiliary Service | `auxiliary-service` | Consumes downstream events, sends email, handles HRBP alerts, payout submission, payroll results, idempotency. |
| Payroll Broker | `payroll-broker` | Consumes approval events, routes payout to provider, exposes OAuth/webhook endpoints, supports pay lookup. |
| Fraud Analytics Job | `fraud-analytics-job` | Runs weekly graph detection, model training, holiday sync, and forecast model generation. |
| MCP / Analytics services | `MCP_Servers` | SQL and export service support for analytics/investigation workflows. |

## Tenant Boundary

The tenant boundary is enforced through:

- Entra ID `tid` claim.
- `Tenants.AzureAdTenantId` allowlist lookup.
- Internal `TenantId` attached to the authenticated user context.
- Tenant-scoped user lookup.
- Tenant-scoped SQL helper functions and analytics queries.
- Domain-based tenant branding and wrong-portal detection.
- Tenant-specific configuration and categories.

## Core Workflow

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Frontend
    participant API as Backend API
    participant SB as Service Bus
    participant IC as Integrity Worker
    participant AUX as Auxiliary Worker
    participant HR as HRBP
    participant M as Manager
    participant PB as Payroll Broker

    U->>FE: Submit nomination
    FE->>API: POST /api/nominations
    API->>API: Validate tenant, amount, category, manager, description structure
    API->>SB: Publish nomination.submitted
    SB->>IC: Deliver to fraud-processor subscription
    IC->>IC: Semantic and ML fraud assessment
    alt clean
        IC->>SB: Publish nomination.created
        SB->>AUX: Deliver to email-processor
        AUX->>M: Send approval email
        M->>API: Approve/reject
    else flagged
        IC->>SB: Publish nomination.fraud-flagged
        SB->>AUX: Deliver HRBP notification
        AUX->>HR: Send HRBP review alert
        HR->>API: Approve/reject HRBP review
    else description rejected
        IC->>SB: Publish nomination.description-rejected
        SB->>AUX: Notify nominator
    end
    API->>SB: Publish nomination.approved
    SB->>AUX: Outcome email and payout submit
    SB->>PB: Payroll processor
    PB->>SB: Publish payroll.accepted or payroll.failed
```

## Service Bus Topology

| Topic or subscription | Filter | Consumer |
| --- | --- | --- |
| `award-events` topic | All award lifecycle events | All publishers send here. |
| `fraud-processor` subscription | `event_type = 'nomination.submitted'` | Integrity Check Worker. |
| `email-processor` subscription | `event_type != 'nomination.submitted'` | Auxiliary Service. |
| `payroll-processor` subscription | `event_type = 'nomination.approved'` | Payroll Broker. |

## Deployment Topology

```mermaid
flowchart TB
    Internet["Users and external providers"] --> AFD["Azure Front Door Standard<br/>WAF, health probes, CORS rule set"]
    AFD --> API1["Backend API primary ACA"]
    AFD --> API2["Backend API secondary ACA"]
    AFD --> Payroll["Payroll Broker ACA"]
    SWA["Azure Static Web Apps"] --> AFD
    API1 --> SQL["Azure SQL"]
    API2 --> SQL
    API1 --> SB["Service Bus"]
    API2 --> SB
    Workers["Integrity, Auxiliary, Analytics Job"] --> SB
    Workers --> SQL
    Workers --> Storage["Blob Storage"]
    KV["Key Vault"] --> API1
    KV --> API2
    KV --> Workers
    AppInsights["Application Insights + Log Analytics"] <-->|Telemetry| API1
    AppInsights <-->|Telemetry| Workers
```

## External Dependencies

- Microsoft Entra ID for authentication and app roles.
- Azure SQL for transactional and analytics persistence.
- Azure Service Bus for workflow events.
- Azure Blob Storage for models, certificates, certificate templates, and exports.
- Azure OpenAI for AI analytics, investigation, and explanation generation.
- SMTP/email provider for workflow notifications.
- Gusto/Rippling/Workday-style payroll providers for payout execution.
- Cloudflare/Azure DNS assets for public tenant and payroll broker domains.

