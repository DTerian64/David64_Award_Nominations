# Roadmap: Award Nomination App to Integrity Sentinel

## Strategic Thesis

The Award Nomination App is a focused product that solves recognition governance. Integrity Sentinel is the broader platform opportunity: detecting and managing integrity risk across enterprise workflows.

The reusable pattern is:

```text
Business workflow + tenant rules + event trail + AI/graph risk detection + human review + audit + executive analytics
```

## Current Foundation

The current application already includes many platform primitives:

- Tenant registry and configuration.
- Identity and role-aware access.
- Workflow state machine.
- Service Bus event backbone.
- Idempotent workers.
- Fraud scoring.
- Graph pattern detection.
- Human review queue.
- AI analytics agents.
- Export capabilities.
- Payroll/provider integration.
- Observability.
- Terraform deployment.

These primitives can be generalized beyond award nominations.

## Expansion Domains

Potential Integrity Sentinel domains:

| Domain | Example risk patterns |
| --- | --- |
| Awards and recognition | Rings, favoritism, duplicate text, excessive awards. |
| Payroll adjustments | Unusual off-cycle payments, repeated adjustments, approver concentration. |
| Expense reimbursement | Duplicate receipts, reciprocal approvals, outlier categories. |
| Procurement | Vendor favoritism, split purchases, approval rings. |
| Grants | Repeated awardees, reviewer conflicts, eligibility anomalies. |
| Access requests | Privilege escalation, repeated emergency access, approver affinity. |
| Vendor onboarding | Shared addresses, suspicious ownership patterns, duplicate entities. |

## Product Roadmap

### Phase 1 - Harden Award Nomination Product

- Complete tenant admin UI for configuration.
- Add role assignment UI.
- Improve audit logging coverage.
- Add database row-level security.
- Add payroll lookup audit.
- Add model metadata/version table.
- Add test automation for end-to-end workflows.
- Improve onboarding scripts and validation.
- Add tenant offboarding process.

### Phase 2 - Enterprise Readiness

- Formalize security questionnaires.
- Build SOC 2 readiness evidence map.
- Add centralized configuration change audit.
- Add tenant usage and billing metrics.
- Add data retention controls.
- Add disaster recovery tests.
- Add managed customer onboarding playbook.
- Add support runbook library.

### Phase 3 - Integrity Platform Layer

- Generalize event model to multiple workflow domains.
- Define a common integrity finding schema.
- Define reusable reviewer queue abstractions.
- Build tenant policy engine.
- Build cross-domain graph model.
- Build common case/investigation workspace.
- Build reusable export and notification workflows.

### Phase 4 - Advanced Intelligence

- Tenant-specific model selection and threshold optimization.
- Drift detection and model monitoring UI.
- LLM-generated investigation summaries.
- Evidence packs for audits.
- Cross-domain entity resolution.
- Reviewer workload forecasting.
- Scenario simulation for policy changes.

### Phase 5 - Ecosystem and Marketplace

- HRIS connectors.
- Payroll connectors.
- Expense/procurement connectors.
- Power BI and SIEM exports.
- Tenant webhook subscriptions.
- Partner integration toolkit.
- Compliance templates.

## Key Architectural Refactors for Integrity Sentinel

| Current Award App Concept | Platform Concept |
| --- | --- |
| Nomination | Case or transaction. |
| Beneficiary/nominator/approver | Actors and relationships. |
| HRBP review | Human review queue. |
| Fraud score | Risk score. |
| GraphPatternFindings | Integrity findings. |
| Tenant config | Policy configuration. |
| Service Bus award events | Domain event backbone. |
| Payroll broker | External action connector. |
| AI ask/investigate | Investigation assistant. |

## Recommended Next Product Investments

Highest-value next steps:

1. Build a tenant admin configuration surface.
2. Add full audit coverage for configuration, role, payroll, and model operations.
3. Add model card and model registry metadata.
4. Add automated integration tests for Service Bus workflows.
5. Add a generic integrity finding schema that can support multiple future domains.
6. Turn HRBP review into a reusable review queue component.
7. Package demo data and scripts for repeatable investor/customer demos.

## Investor/Customer Narrative

The Award Nomination App proves the platform in a narrow but meaningful domain:

- The workflow is understandable.
- The money movement is real.
- The risk patterns are concrete.
- The human review process is credible.
- The architecture is enterprise-friendly.

Integrity Sentinel expands that proven pattern to any process where organizations need to know whether approvals, awards, payments, or access decisions are fair, explainable, and auditable.

