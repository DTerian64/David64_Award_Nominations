# Award Nomination App Documentation Map

This folder is a clean, reverse-engineered documentation package for the Award Nomination App. It is intentionally separate from the existing `Documentation` folder.

The documentation is organized as four named volumes. The numeric prefixes keep the folders in reading order; the folder names describe the purpose of each collection.

Structured diagram sources are stored separately from the prose documents:

| Location | Purpose |
| --- | --- |
| [diagrams/structurizr/workspace.dsl](../diagrams/structurizr/workspace.dsl) | Structurizr DSL source for system context, container, data model, workflow, payroll, model lifecycle, and deployment views. |
| [diagrams/structurizr/README.md](diagrams/structurizr/README.md) | Rendering instructions and mapping from view keys to documentation diagrams. |
| [diagrams/exports](diagrams/exports/README.md) | Rendered SVG diagram exports embedded in the Markdown documents. |

## 01 - Business and Product

| Document | Audience | Purpose |
| --- | --- | --- |
| [01_Executive_Overview.md](01_Business_and_Product/01_Executive_Overview.md) | Executives, investors, grant reviewers, tenant sponsors | Explains the product, value proposition, current state, and strategic direction. |
| [02_Business_Architecture.md](01_Business_and_Product/02_Business_Architecture.md) | Business owners, product leaders, HR stakeholders | Defines business capabilities, personas, workflow, tenant model, and governance processes. |
| [03_Product_Positioning.md](01_Business_and_Product/03_Product_Positioning.md) | Sales, marketing, product strategy, buyers | Positions the product in the market and describes differentiators. |
| [17_Demo_and_Sandbox_Guide.md](01_Business_and_Product/17_Demo_and_Sandbox_Guide.md) | Demo hosts, evaluators, pilot tenants | Provides demo scenarios, roles, and validation paths. |
| [18_Roadmap_to_Integrity_Sentinel.md](01_Business_and_Product/18_Roadmap_to_Integrity_Sentinel.md) | Product leadership, investors, architecture reviewers | Connects the Award Nomination App to the broader Integrity Sentinel direction. |

## 02 - Solution and Technical Architecture

| Document | Audience | Purpose |
| --- | --- | --- |
| [04_Solution_Architecture.md](02_Solution_and_Technical_Architecture/04_Solution_Architecture.md) | Architects, senior engineers, technical reviewers | High-level component, actor, deployment, and workflow architecture. |
| [05_Technical_Architecture.md](02_Solution_and_Technical_Architecture/05_Technical_Architecture.md) | Engineering teams, platform teams | Frontend, backend, service, identity, observability, and infrastructure architecture. |
| [07_Data_Architecture.md](02_Solution_and_Technical_Architecture/07_Data_Architecture.md) | Data engineers, DBAs, analytics reviewers | Conceptual, logical, and physical data design. |
| [08_AI_ML_Architecture_and_Model_Governance.md](02_Solution_and_Technical_Architecture/08_AI_ML_Architecture_and_Model_Governance.md) | ML reviewers, risk teams, architects | Fraud, graph, semantic, forecasting, model governance, and human review design. |
| [09_Security_Architecture.md](02_Solution_and_Technical_Architecture/09_Security_Architecture.md) | Security, enterprise buyers, platform reviewers | Identity, authorization, tenant isolation, secrets, network, audit, and threat model. |
| [10_Integration_Architecture.md](02_Solution_and_Technical_Architecture/10_Integration_Architecture.md) | Integration teams, implementation partners | Payroll, identity, email, analytics export, and future HRIS integration patterns. |

## 03 - Engineering Design and API

| Document | Audience | Purpose |
| --- | --- | --- |
| [06_Detailed_Design.md](03_Engineering_Design_and_API/06_Detailed_Design.md) | Developers, maintainers, implementation reviewers | Endpoint, workflow, async processing, configuration, and implementation detail. |
| [14_API_Specification.md](03_Engineering_Design_and_API/14_API_Specification.md) | Frontend developers, integrators, testers | Human-readable API reference grounded in the FastAPI routers. |

## 04 - Operations, Governance, and Readiness

| Document | Audience | Purpose |
| --- | --- | --- |
| [11_Deployment_and_DevOps_Architecture.md](04_Operations_Governance_and_Readiness/11_Deployment_and_DevOps_Architecture.md) | DevOps, platform engineers, release managers | Azure topology, Terraform modules, GitHub Actions, environments, rollback. |
| [12_Observability_and_Operations_Guide.md](04_Operations_Governance_and_Readiness/12_Observability_and_Operations_Guide.md) | Operators, SRE, support | Logging, metrics, traces, dashboards, health checks, and runbooks. |
| [13_Tenant_Onboarding_Guide.md](04_Operations_Governance_and_Readiness/13_Tenant_Onboarding_Guide.md) | Customer success, tenant admins, implementation teams | Tenant setup, branding, user import, roles, workflow, go-live checklist. |
| [15_Test_Strategy_and_Quality_Plan.md](04_Operations_Governance_and_Readiness/15_Test_Strategy_and_Quality_Plan.md) | QA, engineering leads, release approvers | Test layers, scenarios, non-functional validation, release gates. |
| [16_Compliance_Risk_and_Governance.md](04_Operations_Governance_and_Readiness/16_Compliance_Risk_and_Governance.md) | Risk, legal, security, enterprise buyers | Risk register, privacy, AI governance, auditability, SOC 2 readiness direction. |

## Source Baseline

These documents were reverse engineered from the repository as of July 7, 2026, with emphasis on:

- `frontend/src` React, MSAL, tenant configuration, analytics, HRBP, payroll, and i18n code.
- `backend` FastAPI routers, auth, SQL helper, fraud model loader, and Alembic migrations.
- `integrity-check` async submission-time fraud and description quality worker.
- `auxiliary-service` Service Bus notification and payout orchestration worker.
- `payroll-broker` provider broker, payroll worker, Gusto/Rippling provider structure.
- `fraud-analytics-job` weekly graph, ML training, holiday sync, and forecasting job.
- `terraform`, `Deployment`, `.github/workflows`, `Observability`, and analytics KQL assets.

## Known Documentation Assumptions

- The product is treated as a tenant-configurable SaaS platform, not only a demo application.
- Azure SQL is the system of record.
- Entra ID is the primary identity provider.
- Azure Service Bus is the event backbone.
- Azure Container Apps, Static Web Apps, Front Door, Key Vault, Storage, Application Insights, and Log Analytics are the target Azure platform components.
- Payroll integrations include implemented broker support for Gusto and Rippling structure, with Workday references present as webhook/proxy patterns and future integration direction.
