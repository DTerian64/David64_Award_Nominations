# Data Processing Agreement (Template)

> **NOT LEGAL ADVICE — TEMPLATE ONLY.** This document is a structural starting point, not legal advice, and Terian Services Inc. / the author is not a law firm. It must be reviewed, completed, and adapted by qualified legal counsel — for your jurisdiction, your Master Services Agreement, and applicable data-protection laws (e.g., GDPR, UK GDPR, CCPA/CPRA) including Standard Contractual Clauses for any cross-border transfers — before it is offered to or executed with a customer. Bracketed `[[ ]]` fields are meant to be completed per deal.

This Data Processing Agreement ("**DPA**") is entered into by and between:

- **`[[Customer legal name]]`** ("**Customer**" / "**Controller**"), and
- **Terian Services Inc.** ("**Provider**" / "**Processor**"),

and is effective as of **`[[Effective Date]]`** (the "**Effective Date**"). This DPA is incorporated into and forms part of the **`[[Master Services Agreement / Subscription Agreement]]`** between the parties (the "**Agreement**") governing the Customer's use of the Award Nomination System (the "**Service**").

---

## 1. Definitions

Terms not defined here have the meaning given in the Agreement or in Applicable Data Protection Laws.

- **"Applicable Data Protection Laws"** — all laws and regulations applicable to the processing of Personal Data under the Agreement, including, as applicable, the EU GDPR, UK GDPR, and US state privacy laws (e.g., CCPA/CPRA).
- **"Personal Data"** — any information relating to an identified or identifiable natural person that Provider processes on behalf of Customer under the Agreement.
- **"Processing"**, **"Controller"**, **"Processor"**, **"Data Subject"**, **"Personal Data Breach"**, and **"Supervisory Authority"** — as defined in Applicable Data Protection Laws.
- **"Subprocessor"** — any third party engaged by Provider to process Personal Data on Customer's behalf.

## 2. Roles and scope

The parties agree that, for Personal Data processed under the Agreement, **Customer is the Controller** and **Provider is the Processor** (or, where Customer is itself a processor, Provider is a sub-processor). The subject matter, duration, nature and purpose of processing, the types of Personal Data, and categories of Data Subjects are described in **Annex 1**.

## 3. Processing instructions

Provider shall process Personal Data only on Customer's documented instructions, including as set out in the Agreement and this DPA, and as necessary to provide and support the Service — unless required to do otherwise by law, in which case Provider shall (where permitted) inform Customer of that legal requirement before processing. Provider shall promptly inform Customer if, in its opinion, an instruction infringes Applicable Data Protection Laws.

## 4. Confidentiality

Provider shall ensure that personnel authorized to process Personal Data are bound by appropriate obligations of confidentiality and are trained on their data-protection responsibilities.

## 5. Security

Provider shall implement and maintain appropriate technical and organizational measures to protect Personal Data against unauthorized or unlawful processing and accidental loss, destruction, or damage, as described in **Annex 2**, taking into account the state of the art, the costs of implementation, and the nature, scope, context, and purposes of processing.

## 6. Subprocessors

- Customer provides **general authorization** for Provider to engage Subprocessors to support the Service. The current Subprocessors are listed in **Annex 3**.
- Provider shall impose data-protection obligations on each Subprocessor that are no less protective than those in this DPA and remains responsible for its Subprocessors' performance.
- Provider shall give Customer at least **`[[30]]` days'** notice of any intended addition or replacement of a Subprocessor, giving Customer the opportunity to object on reasonable data-protection grounds.

## 7. Assistance with Data Subject rights

Taking into account the nature of the processing, Provider shall assist Customer by appropriate technical and organizational measures, insofar as possible, in fulfilling Customer's obligations to respond to requests from Data Subjects exercising their rights (access, rectification, erasure, restriction, portability, and objection). Where a Data Subject contacts Provider directly, Provider shall (unless legally prohibited) redirect the request to Customer.

## 8. Personal Data Breach

Provider shall notify Customer **without undue delay, and in any event within seventy-two (72) hours**, after confirming a Personal Data Breach affecting Customer's Personal Data. Such notice shall describe, to the extent known, the nature of the breach, the categories and approximate number of Data Subjects and records affected, the likely consequences, and the measures taken or proposed to address it. Provider shall reasonably cooperate with Customer in investigating and remediating the breach.

## 9. Data protection impact assessments

Provider shall provide reasonable assistance to Customer with any data protection impact assessments and prior consultations with Supervisory Authorities that Customer is required to carry out, taking into account the nature of processing and information available to Provider.

## 10. International transfers

Provider shall not transfer Personal Data outside of `[[the United States / the EEA / applicable region]]` except in compliance with Applicable Data Protection Laws, including, where required, the execution of Standard Contractual Clauses or reliance on another valid transfer mechanism. `[[Attach/reference SCCs as applicable.]]`

## 11. Audits

Provider shall make available to Customer information reasonably necessary to demonstrate compliance with this DPA and shall allow for and contribute to audits, including inspections, conducted by Customer or an auditor mandated by Customer, no more than **`[[once per year]]`** (except where required by a Supervisory Authority or following a Personal Data Breach), subject to reasonable notice, confidentiality, and Provider's security policies. Provider may satisfy audit requests by providing third-party certifications or reports where available.

## 12. Return and deletion

Upon termination or expiry of the Agreement, Provider shall, at Customer's choice, return or delete all Personal Data processed on Customer's behalf and **delete existing copies within sixty (60) days**, unless retention is required by law. Provider shall also support **individual-record erasure** on Customer's request during the term.

## 13. Liability

Each party's liability under this DPA is subject to the limitations and exclusions of liability set out in the Agreement.

## 14. Term

This DPA takes effect on the Effective Date and continues for as long as Provider processes Personal Data on behalf of Customer under the Agreement.

## 15. Governing law and precedence

This DPA is governed by the law specified in the Agreement, or, if none, by the laws of **`[[governing jurisdiction]]`**. In the event of conflict between this DPA and the Agreement regarding the processing of Personal Data, this DPA prevails.

---

## Execution

IN WITNESS WHEREOF, the parties have caused this Data Processing Agreement to be executed by their duly authorized representatives as of the Effective Date.

| Customer / Controller | Provider / Processor |
|---|---|
| **`[[Customer legal name]]`** | **Terian Services Inc.** |
| Signature: _______________________ | Signature: _______________________ |
| Name: `[[name]]` | Name: `[[name]]` |
| Title: `[[title]]` | Title: `[[title]]` |
| Date: `[[date]]` | Date: `[[date]]` |

---

## Annex 1 — Details of processing

| Item | Description |
|---|---|
| **Subject matter** | Provision of the Award Nomination System (employee recognition, approval, integrity analytics, and payout coordination). |
| **Duration** | For the term of the Agreement plus the retention/deletion period in Section 12. |
| **Nature and purpose** | Hosting, processing, and analysis of nomination and recognition data to operate the Service, including fraud/integrity detection and payroll payout coordination. |
| **Types of Personal Data** | `[[e.g., names, work email/UPN, employee/department identifiers, manager relationships, nomination content, award amounts, payroll identifiers necessary for payout]]` |
| **Categories of Data Subjects** | `[[Customer's employees and personnel (nominators, beneficiaries, approvers, administrators)]]` |
| **Special categories** | `[[None intended / specify if any]]` |

## Annex 2 — Technical and organizational measures

Provider maintains the security measures summarized in the **Security Overview** (available on request), including: single public ingress via Azure Front Door (WAF, TLS); private-endpoint-only data services on the Azure backbone; Microsoft Entra ID SSO with role-based access control; per-tenant data isolation; encryption in transit (TLS 1.2+) and at rest (TDE, storage encryption); secrets in Azure Key Vault with managed identities; centralized logging, monitoring, and audit trails; active-active multi-region hosting with point-in-time database restore. `[[Confirm and keep aligned with the Security Overview.]]`

## Annex 3 — Approved Subprocessors

| Subprocessor | Purpose | Location |
|---|---|---|
| Microsoft Azure | Cloud hosting, database, storage, Azure OpenAI | `[[US regions]]` |
| `[[Gusto / Rippling]]` | Payroll payout processing | `[[US]]` |
| `[[Email/SMTP provider]]` | Transactional email notifications | `[[region]]` |
| `[[Add others as applicable]]` | | |

---

*Terian Services Inc. · Data Processing Agreement (Template) · Confidential — for negotiation. Complete all `[[ ]]` fields and obtain legal review before use.*
