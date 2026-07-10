# Requirements — Award Nomination System: Total-Cost / ROI Calculator (terianix.ai)

**Owner:** David Terian / Terian Services Inc.
**Status:** Ready to build (new work session)
**This document is self-contained** — it assumes no prior context. Hand it to a fresh session as the brief.

---

## 1. Purpose

Build an interactive **Total-Cost / ROI calculator** for the **Award Nomination System**, hosted on **terianix.ai**, that lets a prospective buyer enter a few numbers about their organization and instantly see the estimated value, net benefit, ROI %, and payback period of adopting the product — and, optionally, capture their inputs as a sales lead.

It replaces the idea of emailing a static Excel "ROI worksheet": a web tool stays in sync with live pricing, needs no attachment, and captures leads.

## 2. Product context (so the model is grounded)

The **Award Nomination System** is a multi-tenant SaaS platform for **monetary employee recognition** with an **AI integrity engine** at its core. It:

- Runs the full recognition workflow: nomination → manager/HR approval → payout.
- Screens every award with a **multi-model AI engine** (rules + Random Forest + Isolation Forest + graph collusion detection + NLP), blocking or flagging fraud, favoritism, reciprocal "rings," and copy-paste/quid-pro-quo abuse **before payout**.
- **Forecasts** recognition spend and tracks it against budget.
- **Pays out through the customer's payroll** (Gusto, Rippling, Workday-style), not points/gift cards.

**Pricing (source of truth: https://www.terianix.ai/pricing/award-nomination):**

| Plan | Users | Price (billed annually) | Monthly billing |
|---|---|---|---|
| Starter | up to 50 | $149/mo | $189/mo |
| Professional (most popular) | 50–500 | $499/mo | $624/mo |
| Enterprise | 500+ | Custom | Custom |

> The calculator must read pricing from a **single config/source** shared with the pricing page so it never drifts. If pricing changes, the calculator updates automatically.

## 3. Goals & success criteria

- A prospect can get a **credible ROI estimate in under a minute**, with sensible defaults so a result shows immediately (before they change anything).
- **Conservative and transparent** — assumptions are visible and editable; no black-box or hype numbers (this is a trust tool, and technical/finance buyers will scrutinize it).
- **Lead capture** — optionally collect the prospect's email + inputs and route to the CRM / contact flow.
- **Always current** with live pricing.
- **Responsive and accessible** (mobile-friendly, WCAG-minded).
- Embeddable on the **Pricing page** (primary) with a CTA entry point from the **Award Nomination product page**.

## 4. Placement & entry points

- **Primary:** the Award Nomination **Pricing** page (https://www.terianix.ai/pricing/award-nomination) — a "Calculate your ROI" section/CTA.
- **Secondary:** a CTA on the Award Nomination **product** page linking to the calculator.
- Consider a shareable deep link that pre-fills inputs (for sales to send a pre-filled estimate).

## 5. The ROI model (implement exactly)

### 5.1 Inputs (user-entered)

| Input | Type | Default | Range | Help text |
|---|---|---|---|---|
| Number of employees | integer | 300 | 1–100,000 | Used to suggest a plan. |
| Annual recognition budget ($) | currency | 500,000 | ≥ 0 | Total monetary awards paid per year. |
| Nominations per year | integer | 1,500 | ≥ 0 | Approx. award nominations submitted annually. |
| Current avg approval time (hrs per nomination) | number | 2.0 | 0–100 | Time approvers/HR spend chasing/processing each. |
| Loaded hourly cost of approvers/HR ($/hr) | currency | 60 | ≥ 0 | Fully-loaded cost (salary + overhead). |
| Plan | select | auto by employees | Starter/Professional/Enterprise | Auto-suggest; user can override. |
| Billing | toggle | Annual | Annual / Monthly | Drives the price used. |

### 5.2 Assumptions (editable defaults, shown with tooltips)

| Assumption | Default | Rationale |
|---|---|---|
| % of budget lost to gaming/fraud/favoritism | **4%** (conservative; allow 3–5% range) | Kept modest for credibility; user can adjust. |
| Detection effectiveness (share of that leakage prevented) | **70%** | The engine reduces but won't claim to eliminate. |
| Approval-time reduction with the platform | **75%** | One-click, SLA-tracked review vs. manual chase. |
| Include budget-overrun avoidance? | Off by default (optional lever) | Softer; leave optional to stay conservative. |

### 5.3 Formulas

```
leakage_prevented        = annual_budget × leakage_pct × detection_effectiveness
new_approval_time        = current_approval_time × (1 − approval_time_reduction)
admin_hours_saved        = nominations_per_year × (current_approval_time − new_approval_time)
admin_cost_saved         = admin_hours_saved × loaded_hourly_cost

annual_value             = leakage_prevented + admin_cost_saved
                           (+ optional budget_overrun_avoidance if enabled)

annual_subscription_cost = selected_plan_monthly_price × 12    (Enterprise → "Contact us")

net_annual_benefit       = annual_value − annual_subscription_cost
roi_percent              = net_annual_benefit / annual_subscription_cost × 100
payback_months           = annual_subscription_cost / (annual_value / 12)
```

- If **Enterprise** (custom pricing) is selected, show value levers but replace cost/ROI with a **"Contact sales for pricing"** CTA (don't fabricate a price).
- Guard against divide-by-zero (e.g., annual_value = 0 → payback = "n/a").
- Round money to whole dollars; ROI to whole %; payback to 1 decimal.

### 5.4 Outputs (display)

- **Headline cards:** Estimated annual value, Net annual benefit, ROI %, Payback (months).
- **Breakdown:** leakage prevented, admin cost saved (and overrun avoidance if enabled), vs. subscription cost.
- **Simple chart:** annual value vs. annual cost (bar), or a payback timeline.
- **Assumptions panel:** visible and editable, with tooltips explaining each.
- **Disclaimer (required):** "Estimate for illustration only, based on your inputs and stated assumptions; not a guarantee of results."

## 6. UX / interaction

- Live recompute on every input change (no "calculate" button needed, or provide one for clarity).
- Ship with defaults that produce a sensible result on first load.
- Plan auto-selects from employee count; user can override.
- Number fields + sliders where helpful; clear currency formatting; input validation.
- "Reset to defaults" control.
- Optional "Conservative / Typical" preset toggle that sets the assumption bundle.

## 7. Lead capture (optional but recommended)

- Do **not** gate the estimate behind a form (friction kills usage). Show the result, then offer:
  - **"Email me these results"** and/or **"Book a walkthrough"** → capture email + the input/assumption set + computed outputs.
- Route leads to the existing contact flow (https://www.terianix.ai/contact) or the CRM/endpoint `[[confirm CRM / form endpoint]]`.
- Fire analytics events (calculator viewed, computed, lead submitted) `[[confirm analytics platform]]`.

## 8. Technical considerations

- Match the **existing terianix.ai stack** `[[confirm framework — e.g., Next.js/React, Webflow, WordPress, static]]`; build as a self-contained, embeddable component.
- **All math client-side** (fast, no backend needed for the estimate). Backend/serverless only for lead capture.
- **Pricing as shared config** with the pricing page (single source of truth).
- Responsive + accessible (keyboard, labels, contrast).
- Currency/localization: `[[USD only for now? or multi-currency?]]`

## 9. Non-goals

- Not a binding quote, contract, or guaranteed-savings claim.
- Not a replacement for a sales conversation on Enterprise pricing.

## 10. Deliverables

1. The calculator component, integrated on the Pricing page (+ product-page CTA).
2. Live-pricing wiring (shared config).
3. Lead-capture + analytics wiring.
4. A short "How this estimate works" explainer (reuses §5).

## 11. Open decisions for David (answer at kickoff)

1. Confirm default **leakage %** (proposed 4%) and **detection effectiveness** (70%).
2. Include the **budget-overrun avoidance** lever, or keep it off for conservatism?
3. **Gate results** behind email, or open with a soft "email/book" CTA? (Recommended: open.)
4. **CRM / form endpoint** and **analytics platform** for lead capture.
5. **terianix.ai stack/framework** the component must fit.
6. **Currency** scope (USD only vs. multi-currency).
7. Placement details: dedicated calculator page vs. embedded section on Pricing.

## 12. References

- Pricing: https://www.terianix.ai/pricing/award-nomination
- Product/company: https://www.terianix.ai/
- Contact: https://www.terianix.ai/contact
- Messaging consistency: see the buyer packet in this repo — `Documentation_Structured/Markdowns/01_Business_and_Product/01_Decision_Maker_Overview.md` (value levers, differentiators) and `Security_Overview.md`.

---
*Prepared as a hand-off brief. Terian Services Inc. · Award Nomination System.*
