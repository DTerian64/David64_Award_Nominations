# Executive Overview

## Purpose

The Award Nomination App is a tenant-configurable employee recognition and award governance platform. It lets employees nominate colleagues for monetary awards, routes nominations through manager and HRBP review, monitors integrity risk, sends workflow notifications, produces analytics, and connects approved awards to payroll processing.

The system is not just a workflow demo. The current codebase includes:

- A React/Vite frontend with Microsoft Entra ID sign-in, tenant-specific branding, locale, currency, and award rules.
- A FastAPI backend with tenant-aware authentication, role-aware APIs, nomination lifecycle endpoints, analytics endpoints, audit logging, certificate generation, and AI analytics agents.
- Azure SQL data persistence with Alembic-managed schema evolution.
- Azure Service Bus event routing for asynchronous fraud screening, notifications, HRBP escalation, and payroll processing.
- An integrity-check worker for description quality, semantic checks, ML fraud scoring, and HRBP routing.
- An auxiliary worker for notification delivery, idempotent event handling, payout submission, payroll result handling, and SLA alerts.
- A payroll broker with provider routing and OAuth/webhook support for external payroll systems.
- A weekly fraud analytics job for graph-pattern detection, model retraining, holiday sync, and forecasting.
- Terraform and GitHub Actions assets for Azure deployment and observability.

## Problem

Employee recognition programs can become inconsistent, opaque, or vulnerable to abuse when nominations are handled through informal email chains, spreadsheets, or basic approval tools.

Common problems include:

- Favoritism or concentrated awards among a small group.
- Duplicate, copy-paste, or low-effort nominations.
- Reciprocal nominations and nomination rings.
- Awards exceeding local or tenant policy limits.
- Weak audit trails for approvals, rejections, payouts, and administrative impersonation.
- Limited executive visibility into spending, trends, review load, risk, and fairness.
- Manual effort to connect approved awards to payroll.

## Product Summary

The Award Nomination App provides a structured award lifecycle:

1. A user signs in with an organization account.
2. The frontend loads tenant branding, currency, locale, award limits, and award categories.
3. The user submits a nomination for another employee.
4. The backend validates amount, category, manager, tenant isolation, and description rules.
5. A Service Bus event sends the nomination to the integrity-check worker.
6. The worker runs description quality checks and ML fraud scoring.
7. Clean nominations route to the manager for approval.
8. Flagged nominations route to HRBP review.
9. Approved nominations trigger notifications, certificate generation, and payroll submission.
10. Admins review analytics, fraud alerts, graph integrity findings, forecasts, logs, and AI-generated investigation support.

## Differentiators

- Tenant-aware SaaS design: branding, domain, locale, currency, categories, award ranges, and configuration are per tenant.
- Fraud-aware workflow: integrity screening is built into the lifecycle before manager approval.
- Graph analytics: the batch job detects rings, super nominators, deserts, approver affinity, copy-paste patterns, transactional language, and hidden candidates.
- Human-in-the-loop governance: flagged nominations route to HRBP reviewers rather than being silently blocked.
- Operational maturity: Application Insights, OpenTelemetry, structured logs, KQL assets, dashboards, health checks, and idempotent event handling are included.
- Payroll readiness: payroll broker and provider abstraction move the product beyond recognition tracking into payout execution.
- Executive analytics: admins can review overview, spend, top recipients, fraud alerts, approval metrics, diversity metrics, category breakdown, integrity findings, and forecasts.
- AI analytics workspace: admin users can ask questions and run deeper investigations using agent-based analytics tooling.

## Current State

The repository represents a working platform with:

- Frontend app under `frontend`.
- Backend API under `backend`.
- Integrity check worker under `integrity-check`.
- Auxiliary Service Bus worker under `auxiliary-service`.
- Payroll broker under `payroll-broker`.
- Weekly analytics job under `fraud-analytics-job`.
- Terraform infrastructure under `terraform`.
- Observability assets under `Observability` and `analytics/kusto-queries`.
- Database migrations under `backend/alembic/versions`.
- Demo and seed scripts under `scripts`.

## Strategic Direction

The Award Nomination App can stand alone as an employee recognition governance product. It also serves as a strong first domain for a broader Integrity Sentinel platform.

The natural expansion path is:

- From award nomination integrity to cross-domain integrity analytics.
- From fraud alerts to investigation workspaces.
- From HR workflows to enterprise risk signals.
- From tenant-specific rules to tenant-specific models and governance controls.
- From isolated recognition payouts to broader HR, payroll, and compliance integration.

## Executive Recommendation

Position the platform as an AI-enabled recognition governance product for organizations that need more than lightweight nominations. The strongest enterprise story is the combination of:

- Workflow configuration.
- Tenant isolation.
- Auditability.
- AI integrity screening.
- Executive analytics.
- Payroll integration.
- Azure-native operational readiness.

