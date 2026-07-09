# Security Overview — Award Nomination System

**A brief for IT and security reviewers.** Companion to the *Decision-Maker Overview*.

Terian Services Inc. builds the Award Nomination System on Microsoft Azure with a security-first, private-by-design architecture. This one-pager summarizes how your employees' recognition and payroll data is protected. A deeper technical architecture and a completed security questionnaire are available on request.

---

## Hosting & infrastructure

- Runs entirely on **Microsoft Azure**. **Azure Front Door** is the single public entry point (WAF, TLS termination, global load balancing); everything behind it is private.
- Application services run on **VNet-injected Azure Container Apps**. Data services (Azure SQL, Blob Storage, Key Vault, Azure OpenAI) are reachable **only over private endpoints on the Azure backbone** — no public data-plane access.
- **Active-active across two Azure regions** for resilience, with automatic failover.
- Entire environment is **defined as code** (Terraform), so it is reproducible and auditable.

## Identity & access

- Sign-in via **Microsoft Entra ID (Azure AD)** using OAuth2 / OpenID Connect; **SAML SSO (Okta, Azure AD)** on Professional and Enterprise.
- **Role-based access control**; least-privilege by default. Every API request is authenticated and validated (signature, audience, and per-tenant issuer).
- Administrative actions — including any user impersonation — are **logged to an audit trail** (actor, target, action, IP, timestamp).

## Tenant isolation

- Strict **per-tenant data isolation**, enforced on every query by a tenant identifier resolved from the verified sign-in token. **No cross-tenant access.**
- Branding, configuration, categories, and machine-learning models are all **per tenant**.

## Data protection

- **Encryption in transit** (TLS 1.2+) everywhere and **at rest** (Azure SQL Transparent Data Encryption, storage-service encryption).
- Secrets are held in **Azure Key Vault** and injected at runtime via **managed identities** — never stored in code or configuration. Payroll provider credentials are **encrypted** at the application layer.

## AI & data use

- Machine-learning models are **trained per tenant on that tenant's own data only** — never pooled or trained across customers.
- AI analytics use **Azure OpenAI** over tenant-scoped data; under Azure OpenAI terms, customer data is **not used to train the underlying models**.
- Integrity decisions are **explainable and human-governed** — only the highest-risk cases are auto-blocked; the rest are routed to a person with the reasons attached.

## Availability, monitoring & audit

- Centralized logging, metrics, and distributed tracing via **Application Insights and Log Analytics**, with alerting on errors, latency, and anomalies.
- **Traceable event history** for the nomination lifecycle; **point-in-time restore** for the database.

## Payroll & integrations

- Approved award payouts flow to your **payroll provider (Gusto, Rippling, and Workday-style systems)** via provider APIs, with **payment confirmation** returned to the platform. Provider tokens are encrypted; HRIS/real-time integrations available on Enterprise.

## Privacy & compliance

- Enterprise-grade Azure controls today; **formal SOC 2 certification is planned**.
- **Data residency:** Hosted in US Azure regions today; other regions available on request.
- **Data Processing Agreement (DPA):** A DPA is available on request.
- **Breach notification:** We notify affected customers of a confirmed data breach without undue delay, and within **72 hours** of confirming the incident.
- **Data retention & deletion:** Customer data is retained for the life of the subscription and **deleted within 60 days** of termination; individual-record erasure is supported on request.

## Subprocessors *(representative — confirm your current list)*

`[[Microsoft Azure (hosting, database, storage, Azure OpenAI); payroll providers (Gusto, Rippling); email/SMTP provider. Confirm and keep current.]]`

---

**Security questions or a completed questionnaire?** `[[security contact email]]`

*Terian Services Inc. · Award Nomination System · Confidential — for evaluation purposes.*
