"""
utils/certificate.py
====================
Award certificate generation, with lazy generation + blob caching.

Flow (single shared path for both the manager's "Certificate" link and the
optional approval-time email attachment):

    get_or_create_certificate(nomination_id)
        1. Look up the deterministic blob name in the `certificates` container.
        2. If the blob already exists  → return a fresh SAS URL (reuse).
        3. Otherwise: fetch the tenant's template from `certificate-templates`,
           overlay beneficiary name / award / date with reportlab, upload the
           PDF, and return a SAS URL.

The template (a background image) lives on the storage account so it can be
swapped per tenant without a redeploy.  If the template blob is missing we fall
back to a self-contained reportlab design, so certificates always generate.

Storage config comes from the same env vars as agents/skills/exports/blob_storage.py:
    AZURE_STORAGE_ACCOUNT, AZURE_STORAGE_KEY, BLOB_SAS_EXPIRY_HOURS
plus:
    CERTIFICATES_CONTAINER       (default "certificates")
    CERT_TEMPLATES_CONTAINER     (default "certificate-templates")
"""

import io
import logging
import os
from datetime import datetime, timedelta, timezone

from azure.storage.blob import (
    BlobServiceClient, BlobSasPermissions, generate_blob_sas, ContentSettings,
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import utils.sqlhelper2 as sqlhelper

logger = logging.getLogger(__name__)

_ACCOUNT            = os.getenv("AZURE_STORAGE_ACCOUNT")
_KEY                = os.getenv("AZURE_STORAGE_KEY")
_CERT_CONTAINER     = os.getenv("CERTIFICATES_CONTAINER", "certificates")
_TEMPLATE_CONTAINER = os.getenv("CERT_TEMPLATES_CONTAINER", "certificate-templates")
_SAS_EXPIRY_HOURS   = int(os.getenv("BLOB_SAS_EXPIRY_HOURS", "24"))

# Landscape A4
_PAGE_W, _PAGE_H = A4[1], A4[0]

_CURRENCY_SYMBOLS = {
    "USD": "$",  "CAD": "CA$", "AUD": "A$", "EUR": "€",  "GBP": "£",
    "JPY": "¥",  "CNY": "¥",   "KRW": "₩",  "INR": "₹",  "BRL": "R$",
    "MXN": "$",  "CHF": "CHF ",
}


def _fmt_amount(amount: float, currency: str) -> str:
    symbol = _CURRENCY_SYMBOLS.get((currency or "").upper(), f"{(currency or '').upper()} ")
    if (currency or "").upper() in ("JPY", "KRW"):
        return f"{symbol}{amount:,.0f}"
    return f"{symbol}{amount:,.2f}"


def certificate_blob_name(tenant_id: int, nomination_id: int) -> str:
    """
    Deterministic blob name in the certificates container. Stable across calls
    so the cache check is a simple existence test — no DB column required.

    NOTE: the auxiliary worker mirrors this exact convention when it downloads
    the cached PDF to attach to the beneficiary email. Keep them in sync.
    """
    return f"{tenant_id}/nomination_{nomination_id}.pdf"


# ── Blob helpers ──────────────────────────────────────────────────────────────

def _service_client() -> BlobServiceClient:
    conn_str = (
        f"DefaultEndpointsProtocol=https;AccountName={_ACCOUNT};"
        f"AccountKey={_KEY};EndpointSuffix=core.windows.net"
    )
    return BlobServiceClient.from_connection_string(conn_str)


def _sas_url(container: str, blob_name: str) -> str:
    sas_token = generate_blob_sas(
        account_name   = _ACCOUNT,
        container_name = container,
        blob_name      = blob_name,
        account_key    = _KEY,
        permission     = BlobSasPermissions(read=True),
        expiry         = datetime.now(timezone.utc) + timedelta(hours=_SAS_EXPIRY_HOURS),
    )
    return f"https://{_ACCOUNT}.blob.core.windows.net/{container}/{blob_name}?{sas_token}"


def _fetch_template(template_blob: str) -> bytes | None:
    """Download the template image bytes, or None if it isn't there."""
    try:
        client = _service_client().get_container_client(_TEMPLATE_CONTAINER)
        blob = client.get_blob_client(template_blob)
        if not blob.exists():
            logger.warning("Certificate template '%s' not found — using fallback design", template_blob)
            return None
        return blob.download_blob().readall()
    except Exception as e:
        logger.warning("Failed to fetch certificate template '%s': %s", template_blob, e)
        return None


# ── PDF rendering ─────────────────────────────────────────────────────────────

def _render_pdf(data: dict, template_bytes: bytes | None) -> bytes:
    """Build the certificate PDF (landscape A4) and return its bytes."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(_PAGE_W, _PAGE_H))

    if template_bytes:
        try:
            c.drawImage(
                ImageReader(io.BytesIO(template_bytes)),
                0, 0, width=_PAGE_W, height=_PAGE_H,
                preserveAspectRatio=False, mask="auto",
            )
        except Exception as e:
            logger.warning("Template image could not be drawn (%s) — using fallback border", e)
            _draw_fallback_border(c)
    else:
        _draw_fallback_border(c)

    cx = _PAGE_W / 2

    c.setFillColorRGB(0.17, 0.24, 0.31)
    c.setFont("Helvetica-Bold", 34)
    c.drawCentredString(cx, _PAGE_H - 60 * mm, "Certificate of Excellence")

    c.setFont("Helvetica", 14)
    c.drawCentredString(cx, _PAGE_H - 78 * mm, "This certificate is proudly presented to")

    c.setFillColorRGB(0.12, 0.39, 0.37)
    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(cx, _PAGE_H - 98 * mm, data["beneficiary_name"])

    c.setFillColorRGB(0.20, 0.20, 0.20)
    c.setFont("Helvetica", 14)
    amount = _fmt_amount(data["amount"], data["currency"])
    c.drawCentredString(
        cx, _PAGE_H - 114 * mm,
        f"in recognition of an outstanding contribution, with a monetary award of {amount}.",
    )

    if data.get("category_description"):
        c.setFont("Helvetica-Oblique", 12)
        c.drawCentredString(cx, _PAGE_H - 126 * mm, f"Category: {data['category_description']}")

    # Date + signatory footer
    approved = data.get("approved_date")
    date_str = approved.strftime("%B %d, %Y") if isinstance(approved, datetime) else datetime.now(timezone.utc).strftime("%B %d, %Y")

    c.setFillColorRGB(0.30, 0.30, 0.30)
    c.setFont("Helvetica", 11)
    c.drawCentredString(_PAGE_W * 0.30, 38 * mm, date_str)
    c.drawCentredString(_PAGE_W * 0.70, 38 * mm, data.get("approver_name") or "")
    c.setLineWidth(0.6)
    c.line(_PAGE_W * 0.18, 46 * mm, _PAGE_W * 0.42, 46 * mm)
    c.line(_PAGE_W * 0.58, 46 * mm, _PAGE_W * 0.82, 46 * mm)
    c.setFont("Helvetica-Oblique", 9)
    c.drawCentredString(_PAGE_W * 0.30, 32 * mm, "Date")
    c.drawCentredString(_PAGE_W * 0.70, 32 * mm, "Approving Manager")

    c.showPage()
    c.save()
    return buf.getvalue()


def _draw_fallback_border(c: canvas.Canvas) -> None:
    """Decorative double border used when no template image is available."""
    c.setStrokeColorRGB(0.12, 0.39, 0.37)
    c.setLineWidth(3)
    c.rect(10 * mm, 10 * mm, _PAGE_W - 20 * mm, _PAGE_H - 20 * mm)
    c.setLineWidth(0.8)
    c.rect(13 * mm, 13 * mm, _PAGE_W - 26 * mm, _PAGE_H - 26 * mm)


# ── Public API ────────────────────────────────────────────────────────────────

def get_or_create_certificate(nomination_id: int, template_blob: str | None = None) -> dict:
    """
    Return a SAS URL for the nomination's award certificate, generating and
    caching the PDF on first request.

    Args:
        nomination_id: the nomination to certify.
        template_blob: override template name; defaults to the tenant's
                       certificate_config.template_blob.

    Returns:
        {"status": "success", "download_url": str, "blob_name": str, "cached": bool}
        or {"status": "error", "message": str}
    """
    if not _ACCOUNT or not _KEY:
        return {"status": "error", "message": "AZURE_STORAGE_ACCOUNT or AZURE_STORAGE_KEY is not set."}

    data = sqlhelper.get_nomination_for_certificate(nomination_id)
    if not data:
        return {"status": "error", "message": f"Nomination {nomination_id} not found."}

    blob_name = certificate_blob_name(data["tenant_id"], nomination_id)

    try:
        container = _service_client().get_container_client(_CERT_CONTAINER)
        if not container.exists():
            container.create_container()

        blob_client = container.get_blob_client(blob_name)

        # ── Cache hit ─────────────────────────────────────────────────────────
        if blob_client.exists():
            return {
                "status":       "success",
                "download_url": _sas_url(_CERT_CONTAINER, blob_name),
                "blob_name":    blob_name,
                "cached":       True,
            }

        # ── Build + store ─────────────────────────────────────────────────────
        if template_blob is None:
            cfg = sqlhelper.get_tenant_certificate_config(data["tenant_id"])
            template_blob = cfg.template_blob

        pdf_bytes = _render_pdf(data, _fetch_template(template_blob))

        blob_client.upload_blob(
            pdf_bytes,
            overwrite=True,
            content_settings=ContentSettings(
                content_type="application/pdf",
                content_disposition=f'inline; filename="certificate_nomination_{nomination_id}.pdf"',
            ),
        )

        return {
            "status":       "success",
            "download_url": _sas_url(_CERT_CONTAINER, blob_name),
            "blob_name":    blob_name,
            "cached":       False,
        }

    except Exception as e:
        logger.error("Certificate generation failed for nomination %d: %s", nomination_id, e, exc_info=True)
        return {"status": "error", "message": str(e)}
