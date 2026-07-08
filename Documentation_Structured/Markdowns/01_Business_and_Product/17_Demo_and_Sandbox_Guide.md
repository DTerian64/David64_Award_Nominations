# Demo and Sandbox Guide

## Demo Purpose

The sandbox demonstrates the Award Nomination App as a working tenant-configurable recognition governance platform.

Use the demo to show:

- Tenant-branded sign-in.
- Nomination submission.
- Manager approvals.
- HRBP review for flagged nominations.
- Admin analytics.
- Graph integrity findings.
- Forecasting.
- AI ask/investigate workflows.
- Payroll lookup and payout status where configured.
- Observability and operational maturity.

## Demo Roles

Prepare demo accounts for:

- Standard employee/nominator.
- Manager approver.
- HRBP reviewer.
- PayrollBP user.
- Admin user.
- Optional support user.

Admin user should have Entra app role:

- `AWard_Nomination_Admin`

Application roles in `UserRoles`:

- `HRBP`
- `PayrollBP`
- `Support`

## Demo Storyline

### Story 1 - Employee Recognition

1. Open tenant-branded app.
2. Sign in as a standard user.
3. Show tenant-specific currency, award range, and categories.
4. Submit a thoughtful nomination.
5. Show history status as `Submitted`.
6. Explain async integrity screening.

### Story 2 - Manager Approval

1. Sign in as beneficiary manager.
2. Show pending approval queue.
3. Approve the nomination.
4. Show status update.
5. Show certificate link if enabled.
6. Explain downstream notification and payout processing.

### Story 3 - HRBP Integrity Review

1. Submit or select a nomination designed to trigger fraud review.
2. Show `PendingHRBPReview`.
3. Sign in or impersonate HRBP.
4. Open HRBP queue.
5. Review flags and pair history.
6. Approve to continue manager flow or reject with reason.
7. Explain human-in-the-loop governance.

### Story 4 - Executive Analytics

1. Sign in as admin.
2. Open Analytics.
3. Show overview, spend, top recipients, top nominators.
4. Show fraud alerts and diversity metrics.
5. Show category breakdown.
6. Show forecast with annual budget input.
7. Explain tenant-scoped analytics.

### Story 5 - Integrity Sentinel Preview

1. Open integrity findings.
2. Select a graph run.
3. Show patterns such as ring, super nominator, copy-paste, or approver affinity.
4. Export a finding.
5. Explain how this expands into broader Integrity Sentinel capabilities.

### Story 6 - AI Analytics Workspace

1. Open Ask tab.
2. Ask a question such as: "Which employees received the most awards this quarter?"
3. Show conversation persistence.
4. Use Investigate mode for a deeper question.
5. Show export or notification output if available.

### Story 7 - Payroll Connection

1. Sign in as PayrollBP.
2. Open payroll tab.
3. Select employee/month/year.
4. Show pay profile and off-cycle entries.
5. Explain approved award payout flow through payroll broker.

## Demo Data Requirements

Good demo data should include:

- Multiple departments.
- Multiple managers.
- Realistic nominations over time.
- Approved, rejected, pending, paid, and HRBP review statuses.
- At least one duplicate/low-quality description.
- At least one reciprocal or repeated pair pattern.
- At least one graph finding run.
- Payroll provider sandbox data.
- Forecast history across enough days to show meaningful trend.

## Demo Reset and Seed

Relevant scripts include:

- `scripts/seed_demo.py`
- `scripts/seed_nomination_categories.py`
- `scripts/seed_email_templates.py`
- `scripts/seed_certificate_template.py`
- `scripts/assign_admin_role.py`
- `scripts/resubmit_dlq.py`
- `scripts/gusto_seed.py`
- `scripts/gusto_seed2.py`

Before running seed scripts, confirm:

- Target SQL database.
- Tenant ID.
- Environment variables.
- Whether data will be appended or reset.

## Sandbox Validation Checklist

- Public branding endpoint returns correct tenant branding.
- Login succeeds with demo user.
- Wrong-domain behavior is understood.
- Nomination create works.
- Service Bus events are flowing.
- Integrity worker is running.
- Auxiliary worker sends expected emails.
- HRBP role sees HRBP tab.
- Admin role sees analytics and impersonation.
- PayrollBP role sees payroll tab.
- Fraud analytics job has at least one successful run.
- Integrity findings exist for demo.
- Forecast endpoint returns data.
- Application Insights receives logs.
- Front Door health probes are healthy.

## Known Demo Talking Points

- The system is tenant-ready: branding, locale, currency, categories, award limits, and domain are configurable.
- Fraud scoring does not silently deny awards; it routes questionable items to HRBP review.
- Graph findings show risk patterns that ordinary approval workflows do not see.
- Payroll integration proves the product can drive real business outcomes after approval.
- Observability shows the platform can be operated beyond a prototype.
- The product can evolve into Integrity Sentinel by applying the same workflow plus analytics pattern to other business domains.

