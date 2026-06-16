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
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from azure.storage.blob import (
    BlobServiceClient, BlobSasPermissions, generate_blob_sas, ContentSettings,
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

import utils.sqlhelper2 as sqlhelper
import utils.templating as templating

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


def _fmt_amount_for(amount: float, currency: str, lang: str) -> str:
    """Language-aware amount for the certificate. Korean KRW uses the native
    '원' suffix (the CID font lacks the ₩ glyph, and '원' is idiomatic)."""
    if (lang or "").lower().startswith("ko") and (currency or "").upper() == "KRW":
        return f"{amount:,.0f}원"
    return _fmt_amount(amount, currency)


# ── Localized labels + fonts ──────────────────────────────────────────────────

_DEFAULT_LABELS = {
    "title":           "Certificate of Excellence",
    "presented_to":    "This certificate is proudly presented to",
    "recognition":     "in recognition of an outstanding contribution, with a monetary award of {amount}.",
    "category_label":  "Category",
    "date_label":      "Date",
    "signatory_label": "Approving Manager",
}

_KO_FONT = "HYSMyeongJo-Medium"   # reportlab-bundled Korean CID font (Adobe CMaps)
_registered_fonts: set = set()


def _fonts_for(lang: str) -> dict:
    """Pick reportlab fonts by language. Korean needs a CJK-capable CID font;
    Latin scripts use Helvetica. Falls back to Helvetica if the CID font (or its
    CMap resources) cannot be registered."""
    if (lang or "").lower().startswith("ko"):
        try:
            if _KO_FONT not in _registered_fonts:
                pdfmetrics.registerFont(UnicodeCIDFont(_KO_FONT))
                _registered_fonts.add(_KO_FONT)
            return {"heading": _KO_FONT, "body": _KO_FONT, "italic": _KO_FONT}
        except Exception as e:
            logger.warning("Could not register Korean font %s (%s) — falling back to Helvetica", _KO_FONT, e)
    return {"heading": "Helvetica-Bold", "body": "Helvetica", "italic": "Helvetica-Oblique"}


def _format_date(dt, lang: str) -> str:
    if not isinstance(dt, datetime):
        dt = datetime.now(timezone.utc)
    if (lang or "").lower().startswith("ko"):
        return f"{dt.year}년 {dt.month}월 {dt.day}일"
    return dt.strftime("%B %d, %Y")


def _labels_for(tenant_id: int, lang: str) -> dict:
    """Localized certificate labels from the template store ('certificate' key),
    merged over English defaults. Never raises — falls back to defaults."""
    try:
        row = templating.resolve_raw(tenant_id, "certificate", lang)
        if row and row[1]:
            return {**_DEFAULT_LABELS, **json.loads(row[1])}
    except Exception as e:
        logger.warning("Certificate labels resolve failed (%s) — using defaults", e)
    return dict(_DEFAULT_LABELS)


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

def _render_pdf(data: dict, template_bytes: bytes | None, labels: dict, lang: str) -> bytes:
    """Build the certificate PDF (landscape A4) and return its bytes.

    Labels are localized (from the template store); fonts are chosen by language
    so non-Latin scripts (e.g. Korean) render with a CJK-capable CID font."""
    fonts = _fonts_for(lang)
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
    amount = _fmt_amount_for(data["amount"], data["currency"], lang)

    c.setFillColorRGB(0.17, 0.24, 0.31)
    c.setFont(fonts["heading"], 34)
    c.drawCentredString(cx, _PAGE_H - 60 * mm, labels["title"])

    c.setFont(fonts["body"], 14)
    c.drawCentredString(cx, _PAGE_H - 78 * mm, labels["presented_to"])

    c.setFillColorRGB(0.12, 0.39, 0.37)
    c.setFont(fonts["heading"], 30)
    c.drawCentredString(cx, _PAGE_H - 98 * mm, data["beneficiary_name"])

    c.setFillColorRGB(0.20, 0.20, 0.20)
    c.setFont(fonts["body"], 14)
    c.drawCentredString(cx, _PAGE_H - 114 * mm, labels["recognition"].replace("{amount}", amount))

    if data.get("category_description"):
        c.setFont(fonts["italic"], 12)
        c.drawCentredString(cx, _PAGE_H - 126 * mm, f'{labels["category_label"]}: {data["category_description"]}')

    # Date + signatory footer
    date_str = _format_date(data.get("approved_date"), lang)

    c.setFillColorRGB(0.30, 0.30, 0.30)
    c.setFont(fonts["body"], 11)
    c.drawCentredString(_PAGE_W * 0.30, 38 * mm, date_str)
    c.drawCentredString(_PAGE_W * 0.70, 38 * mm, data.get("approver_name") or "")
    c.setLineWidth(0.6)
    c.line(_PAGE_W * 0.18, 46 * mm, _PAGE_W * 0.42, 46 * mm)
    c.line(_PAGE_W * 0.58, 46 * mm, _PAGE_W * 0.82, 46 * mm)
    c.setFont(fonts["italic"], 9)
    c.drawCentredString(_PAGE_W * 0.30, 32 * mm, labels["date_label"])
    c.drawCentredString(_PAGE_W * 0.70, 32 * mm, labels["signatory_label"])

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

        lang   = sqlhelper.get_tenant_lang(data["tenant_id"])
        labels = _labels_for(data["tenant_id"], lang)
        pdf_bytes = _render_pdf(data, _fetch_template(template_blob), labels, lang)

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
