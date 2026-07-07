# Tenant Onboarding Guide

## Onboarding Objective

Tenant onboarding creates a secure, branded, policy-aligned award nomination environment for a customer organization.

The onboarding process should produce:

- Registered tenant record.
- Tenant domain and branding.
- Entra ID configuration.
- User roster and manager hierarchy.
- Roles for admins, HRBP, PayrollBP, and support.
- Award policy settings.
- Notification templates.
- Optional certificate and payroll configuration.
- Demo or production validation.

## Onboarding Checklist

| Step | Owner | Output |
| --- | --- | --- |
| 1. Tenant intake | Customer success/product | Tenant profile and requirements. |
| 2. Identity setup | Tenant admin/platform | Entra app consent and tenant ID. |
| 3. Tenant record | Platform/admin | `Tenants` row with Azure AD tenant ID and domain. |
| 4. Branding | Tenant/customer success | Logo URL, colors, tagline, site URL. |
| 5. User import | HR/platform | `Users` rows with manager relationships. |
| 6. Role assignment | Tenant admin/platform | Admin, HRBP, PayrollBP, Support roles. |
| 7. Award policy | HR/product | Currency, min/max amount, categories. |
| 8. Description/integrity policy | HR/risk/product | Word count, semantic thresholds, routing thresholds. |
| 9. Email templates | Customer success | Tenant/language templates. |
| 10. Certificate setup | HR/customer success | Template and config if enabled. |
| 11. Payroll setup | Payroll/platform | Provider, OAuth, webhook, token encryption. |
| 12. Validation | QA/customer success | End-to-end test evidence. |
| 13. Go-live | Tenant sponsor | Production readiness signoff. |

## Tenant Intake Questions

- Tenant legal/company name.
- Primary domain and award portal hostname.
- Entra tenant ID.
- Locale and supported languages.
- Currency.
- Minimum and maximum award amounts.
- Award categories.
- Manager approval rules.
- HRBP reviewer list.
- Admin list.
- Payroll provider.
- Certificate requirement.
- Notification sender requirements.
- Data retention requirements.
- Reporting/export requirements.

## Tenant Configuration

Example `Tenants.Config`:

```json
{
  "locale": "en-US",
  "currency": "USD",
  "min_award": 50,
  "max_award": 5000,
  "theme": {
    "primaryColor": "#4f46e5",
    "primaryHoverColor": "#4338ca",
    "primaryLightColor": "#e0e7ff",
    "primaryTextOnDark": "#ffffff"
  }
}
```

Description check config should define:

- Minimum word or character count.
- Boilerplate phrases.
- Embedding model.
- Duplicate threshold.
- Category fit threshold.
- LLM review settings.

Integrity config should define:

- Routing score thresholds.
- Graph pattern thresholds.
- Tenant risk tolerance.

## User Import Requirements

Each user should include:

- Unique user ID or generated ID.
- `userPrincipalName`.
- `userEmail`.
- First name.
- Last name.
- Title or department.
- Manager ID.
- Tenant ID.

Validation:

- No duplicate UPN/email within tenant.
- No missing manager for eligible beneficiaries unless policy allows.
- No manager cycles.
- Admin, HRBP, PayrollBP, and Support users exist in roster.

## Role Assignment

Assign roles:

- `HRBP` for review queue users.
- `PayrollBP` for payroll lookup users.
- `Support` for operational/payroll failure notifications.
- Entra app role `AWard_Nomination_Admin` for admin analytics and impersonation.

## Branding Setup

Configure:

- Tenant domain.
- Site URL.
- Company logo URL.
- Primary color.
- Hover color.
- Light color.
- Text-on-dark color.
- Tagline.

Validation:

- Public `/api/tenant/branding` returns tenant branding by origin.
- Login page reflects branding.
- Authenticated config applies CSS theme variables.
- Wrong-domain detection behaves as expected.

## Award Rules Setup

Configure:

- Currency.
- Minimum award.
- Maximum award.
- Categories, if required.
- Rejection reason policy.
- Certificate behavior.

Validation:

- Frontend displays correct currency and range.
- Backend rejects out-of-range awards.
- Backend requires `CategoryId` when categories exist.

## Payroll Setup

For payroll-connected tenants:

- Create `payroll_providers` row.
- Configure provider name and display name.
- Complete provider OAuth onboarding.
- Store encrypted tokens.
- Configure webhook secrets.
- Validate provider webhook route.
- Test employee pay lookup with PayrollBP role.
- Test approved nomination payout path in sandbox mode first.

## Go-Live Test Scenarios

Minimum validation:

- Sign in as standard user.
- Submit valid nomination.
- Submit invalid amount and confirm rejection.
- Submit missing category when categories exist.
- Confirm nomination enters `Submitted`.
- Confirm integrity worker routes clean nomination to `Pending`.
- Approver receives email.
- Manager approves.
- Nominator receives outcome email.
- Certificate link works if enabled.
- Payroll submission succeeds or expected sandbox result is recorded.
- HRBP flagged path works.
- HRBP approve continues manager workflow.
- HRBP reject notifies nominator.
- Admin analytics load.
- Admin impersonation is audited.

## Post-Go-Live Monitoring

For the first week:

- Monitor login failures.
- Monitor Service Bus dead-letter queues.
- Monitor nomination submission errors.
- Monitor HRBP queue age.
- Monitor email failure rate.
- Monitor payroll failures.
- Review fraud thresholds for false positives.
- Confirm analytics and forecasts populate.

