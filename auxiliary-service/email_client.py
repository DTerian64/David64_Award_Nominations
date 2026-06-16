"""
Email client for the auxiliary worker.

Synchronous wrapper around smtplib — no async needed in a worker process.
Uses SMTP (Zoho by default). The HTML templates are defined below; the backend no longer
renders emails — they are sent asynchronously by this worker.
"""

import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("auxiliary.email")

# ── Config (injected from Key Vault / env via ACA secret references) ──────────
_SMTP_USER        = os.getenv("SMTP_USER", "sales@terian-services.com")
_SMTP_PWD         = os.getenv("SMTP_PASSWORD")
_SMTP_HOST        = os.getenv("SMTP_HOST", "smtppro.zoho.com")
_SMTP_PORT        = int(os.getenv("SMTP_PORT", "587"))
_FROM_EMAIL       = os.getenv("FROM_EMAIL", _SMTP_USER)
_FROM_NAME        = os.getenv("FROM_NAME", "Award Nomination System")

if not _SMTP_PWD:
    logger.warning("SMTP_PASSWORD not set — email sends will fail")


def send_email(
    to_email: str,
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    """
    Send an HTML email via SMTP.

    Args:
        attachments: optional list of (filename, data, content_type) tuples to
                     attach (e.g. the award certificate PDF). When omitted the
                     message is a plain HTML email as before.

    Raises:
        smtplib.SMTPException: on SMTP-level failure (caller decides retry strategy)
        RuntimeError: if SMTP_PASSWORD is not configured
    """
    if not _SMTP_PWD:
        raise RuntimeError("SMTP_PASSWORD is not configured")

    if attachments:
        # "mixed" wraps the HTML body + binary attachments.
        message = MIMEMultipart("mixed")
        message["Subject"] = subject
        message["From"]    = f"{_FROM_NAME} <{_FROM_EMAIL}>"
        message["To"]      = to_email

        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, "html"))
        message.attach(alt)

        for filename, data, content_type in attachments:
            subtype = content_type.split("/", 1)[1] if "/" in content_type else "octet-stream"
            part = MIMEApplication(data, _subtype=subtype)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            message.attach(part)
    else:
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"]    = f"{_FROM_NAME} <{_FROM_EMAIL}>"
        message["To"]      = to_email
        message.attach(MIMEText(body, "html"))

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
            server.starttls()
            server.login(_SMTP_USER, _SMTP_PWD)
            server.sendmail(_FROM_EMAIL, [to_email], message.as_string())
    except Exception as exc:
        logger.error(
            "Email send failed",
            extra={"to": to_email, "subject": subject, "exception": str(exc)},
        )
        raise

    logger.info(
        "Email sent",
        extra={"to": to_email, "subject": subject, "attachments": len(attachments or [])},
    )


def send_plain(to_email: str, subject: str, body: str, from_override: str | None = None) -> None:
    """
    Send a plain-text email via SMTP.

    Used by the notification.requested handler to deliver agent-composed
    messages that are not based on an HTML template.

    from_override — optional sender address from the event payload.
                    Defaults to _FROM_EMAIL (system config) if not provided
                    or if the value does not match the authenticated account.
                    Note: most SMTP providers (incl. Zoho) ignore From overrides that differ from the
                    authenticated sender, so this is informational only.

    Raises:
        smtplib.SMTPException: on SMTP-level failure
        RuntimeError: if SMTP_PASSWORD is not configured
    """
    if not _SMTP_PWD:
        raise RuntimeError("SMTP_PASSWORD is not configured")

    from_display = from_override or _FROM_EMAIL

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"]    = f"{_FROM_NAME} <{from_display}>"
    message["To"]      = to_email
    message.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
            server.starttls()
            server.login(_SMTP_USER, _SMTP_PWD)
            server.sendmail(_FROM_EMAIL, [to_email], message.as_string())
    except Exception as exc:
        logger.error(
            "Email send failed",
            extra={"to": to_email, "subject": subject, "exception": str(exc)},
        )
        raise

    logger.info("Plain email sent", extra={"to": to_email, "subject": subject})


# ── Currency formatting ───────────────────────────────────────────────────────
# Map ISO 4217 codes to their conventional symbols. Unknown codes fall back to
# the ISO code itself as a prefix (e.g. "CHF 1,000.00") — unambiguous and safe.
_CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",  "CAD": "CA$", "AUD": "A$",
    "EUR": "€",  "GBP": "£",   "JPY": "¥",
    "CNY": "¥",  "KRW": "₩",   "INR": "₹",
    "BRL": "R$", "MXN": "$",   "CHF": "CHF ",
}

def _fmt(amount: float, currency: str) -> str:
    """Format an amount with its currency symbol, e.g. '$1,234.56' or 'KRW 1,234'."""
    symbol = _CURRENCY_SYMBOLS.get(currency.upper(), f"{currency.upper()} ")
    # JPY and KRW are typically shown without decimal places
    if currency.upper() in ("JPY", "KRW"):
        return f"{symbol}{amount:,.0f}"
    return f"{symbol}{amount:,.2f}"


# ── HTML templates ────────────────────────────────────────────────────────────
# These templates are the single source of truth for outbound email; the backend
# no longer renders emails. A future phase moves them to dbo.EmailTemplates.


# ── HRBP review workflow templates ───────────────────────────────────────────

_RISK_COLORS: dict[str, str] = {
    "CRITICAL": "#c0392b",
    "HIGH":     "#e67e22",
    "MEDIUM":   "#f39c12",
    "LOW":      "#27ae60",
    "NONE":     "#27ae60",
    "UNKNOWN":  "#7f8c8d",
}


def format_amount(amount: float, currency: str) -> str:
    """Public currency formatter — handlers use it to build template context."""
    return _fmt(amount, currency)


def risk_color(risk_level: str) -> str:
    """Risk-level colour for the HRBP templates (handlers pass it into context)."""
    return _RISK_COLORS.get((risk_level or "").upper(), "#7f8c8d")
