# Beneficiary Notification & Award Certificate — Implementation Plan

**Status:** Proposed — awaiting approval. No code to be written until signed off.
**Date:** 2026-06-15

## Goal

Three additions to the Award Nomination System:

1. After a nomination is approved, **notify the beneficiary** by email (currently only the nominator is emailed).
2. Rename the manager's **Pending Approvals** tab to **My Approvals**, with a heading dropdown that toggles between *Pending Approvals* and *Approved/Rejected*.
3. Approved cards in the *Approved/Rejected* view carry a **Certificate** link that produces an award certificate the manager can present. The certificate template lives on the storage account.

All emailing continues to flow through Service Bus → auxiliary worker. The certificate template and generated PDFs live in blob storage.

## Decisions (locked)

- **Beneficiary email:** always on. Reuses the existing `nomination.approved` event — no new event type, topic, or subscription.
- **Amount visibility:** the beneficiary email **shows** the award amount.
- **Certificate template:** a default reportlab-drawn template, seeded by us into a new blob container; tenants can replace it later.
- **Certificate feature (link + attachment):** **opt-in, default OFF** for existing tenants, gated by tenant config.
- **Per-tenant attachment:** whether the certificate PDF is attached to the beneficiary email is a per-tenant flag, default **false**.
- **Generation strategy:** lazy with caching — `get_or_create_certificate(nomination_id)`. Check the blob; reuse if it exists, otherwise build, store, and serve. The manager link and the (optional) approval-time attachment share this one path.
- **Config location:** new `certificate_config` JSON column on `dbo.Tenants`, mirroring the existing `desc_check_config` pattern.

## Tenant configuration

New column `dbo.Tenants.certificate_config` (NVARCHAR(MAX), nullable, JSON), loaded into a typed dataclass with a never-raises, all-defaults loader — exactly like `DescCheckConfig` / `get_tenant_desc_check_config`.

```
CertificateConfig:
  enabled:                bool  = False   # master switch for link + attachment
  attach_to_beneficiary:  bool  = False   # attach PDF to beneficiary email
  template_blob:          str   = "default_certificate.pdf"
```

A NULL column → all defaults → feature off. Existing tenants are unaffected until they opt in.

## Architecture

### 1. Beneficiary email (auxiliary worker)

`nomination_approved.handle` already reads `beneficiary_email` and `beneficiary_name` from `get_nomination_details()` — no DB read change. After the existing nominator email, on **Approved/Paid only** (never Rejected), send a new template to the beneficiary.

- `email_client.py`: add `render_beneficiary_award(beneficiary_name, dollar_amount, currency, category)`.
- If the tenant has `attach_to_beneficiary = true`, attach the certificate PDF (see §3). This requires:
  - `send_email` gains an optional `attachments` parameter.
  - The worker fetches the cached/generated PDF from blob storage → add `azure-storage-blob` to `auxiliary-service/requirements.txt` (download only; generation stays in the backend).

### 2. "My Approvals" tab (frontend + backend)

- **Frontend** (`App.tsx`): rename the tab label to "My Approvals". Replace the static section heading with a dropdown (*Pending Approvals* / *Approved/Rejected*). *Pending* keeps today's behavior; *Approved/Rejected* fetches the new endpoint and renders decided cards. Approved cards render a **Certificate** link.
- **Backend** (`nominations_router.py` + `sqlhelper2.py`): new `GET /api/nominations/my-approvals` returning nominations where `ApproverId = me` and `Status IN ('Approved','Rejected')`, modeled on `get_pending_nominations_for_approver`.
- **i18n** (`en.json`, `ko.json`): nav label, dropdown options, certificate link label.
- No nav-bar tab is added — this reuses the existing approvals tab.

### 3. Certificate generation (backend)

- New blob container `certificate-templates` (terraform `storage` module + env wiring). We seed `default_certificate.pdf`.
- New backend module (e.g. `utils/certificate.py`) with `get_or_create_certificate(nomination_id) -> (bytes|url)`:
  1. Compute a **deterministic blob name** (e.g. `certificates/{tenant_id}/{nomination_id}.pdf`) — so caching needs **no new Nominations column**.
  2. If that blob exists → return it (reuse).
  3. Else fetch `template_blob` from `certificate-templates`, overlay beneficiary name / award / date with reportlab, upload to the extracts (or a `certificates`) container, return it.
  - Reuses the existing `agents/skills/exports/blob_storage.py` (upload + SAS) and reportlab (already in `backend/requirements.txt`).
- New `GET /api/nominations/{id}/certificate` — authorizes the caller as the approver, calls `get_or_create_certificate`, returns the PDF (stream or SAS redirect). Gated on `certificate_config.enabled`.

## Schema & infra changes

- **Alembic migration:** add `certificate_config NVARCHAR(MAX) NULL` to `dbo.Tenants`. (No `Nominations` change — blob names are deterministic.)
- **Terraform:** add `certificate-templates` container to the `storage` module; expose `CERT_TEMPLATES_CONTAINER` env var to the backend.
- **Seed script:** one-time upload of `default_certificate.pdf` to the new container (under `scripts/` or `Helper-scripts/`).
- **No Service Bus changes** — beneficiary email rides the existing `nomination.approved` event.

## Files touched (estimate)

- `auxiliary-service/handlers/nomination_approved.py` — send beneficiary email; optional attach
- `auxiliary-service/email_client.py` — new template; attachment support
- `auxiliary-service/db.py` — read tenant certificate config; (optional) cert blob download helper
- `auxiliary-service/requirements.txt` — add `azure-storage-blob`
- `backend/routers/nominations_router.py` — `my-approvals` + `certificate` endpoints
- `backend/utils/sqlhelper2.py` — decided-by-approver query; `CertificateConfig` + loader
- `backend/utils/certificate.py` — new; `get_or_create_certificate`
- `backend/routers/schemas.py` — response models as needed
- `frontend/src/App.tsx` — tab rename, dropdown, decided cards, certificate link
- `frontend/src/services/nominationApi.ts`, `types/api.types.ts` — new calls/types
- `frontend/src/i18n/en.json`, `ko.json` — strings
- `terraform/modules/storage/*.tf`, `terraform/environments/dev/main.tf` — container + env var
- Alembic migration; certificate seed script

## Verification plan

- Unit: `render_beneficiary_award` output; `CertificateConfig` loader with NULL / partial / malformed JSON (must default, never raise); deterministic blob-name function.
- Integration: approve a nomination → assert beneficiary receives email (Approved/Paid) and **not** on Rejected; with `attach_to_beneficiary=true`, assert PDF attached.
- Certificate: first `GET …/certificate` generates + stores; second call reuses (assert no regeneration); feature OFF → endpoint/link absent or 403.
- Frontend: dropdown toggles data sources; certificate link only on approved cards.
- Idempotency: re-deliver `nomination.approved` → dispatcher skips, no duplicate emails.

## Open items before build

None blocking. Confirm: (a) the generated-PDF container — reuse `award-nomination-extracts` or a dedicated `certificates` container; (b) certificate delivery from the endpoint — stream bytes vs SAS redirect.
