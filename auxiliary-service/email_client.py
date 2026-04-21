"""
Email client for the auxiliary worker.

Synchronous wrapper around smtplib — no async needed in a worker process.
Uses the same Gmail SMTP config and HTML templates as the backend's email_utils.py.
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("auxiliary.email")

# ── Config (injected from Key Vault via ACA secret references) ────────────────
_GMAIL_USER       = os.getenv("GMAIL_USER", "david.terian@gmail.com")
_GMAIL_APP_PWD    = os.getenv("GMAIL_APP_PASSWORD")
_FROM_EMAIL       = os.getenv("FROM_EMAIL", _GMAIL_USER)
_FROM_NAME        = os.getenv("FROM_NAME", "Award Nomination System")
_SMTP_HOST        = "smtp.gmail.com"
_SMTP_PORT        = 587

if not _GMAIL_APP_PWD:
    logger.warning("GMAIL_APP_PASSWORD not set — email sends will fail")


def send_email(to_email: str, subject: str, body: str) -> None:
    """
    Send an HTML email via Gmail SMTP.

    Raises:
        smtplib.SMTPException: on SMTP-level failure (caller decides retry strategy)
        RuntimeError: if GMAIL_APP_PASSWORD is not configured
    """
    if not _GMAIL_APP_PWD:
        raise RuntimeError("GMAIL_APP_PASSWORD is not configured")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"]    = f"{_FROM_NAME} <{_FROM_EMAIL}>"
    message["To"]      = to_email
    message.attach(MIMEText(body, "html"))

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
        server.starttls()
        server.login(_GMAIL_USER, _GMAIL_APP_PWD)
        server.sendmail(_FROM_EMAIL, [to_email], message.as_string())

    logger.info("Email sent", extra={"to": to_email, "subject": subject})


def send_plain(to_email: str, subject: str, body: str, from_override: str | None = None) -> None:
    """
    Send a plain-text email via Gmail SMTP.

    Used by the notification.requested handler to deliver agent-composed
    messages that are not based on an HTML template.

    from_override — optional sender address from the event payload.
                    Defaults to _FROM_EMAIL (system config) if not provided
                    or if the value does not match the authenticated account.
                    Note: Gmail ignores From overrides that differ from the
                    authenticated sender, so this is informational only.

    Raises:
        smtplib.SMTPException: on SMTP-level failure
        RuntimeError: if GMAIL_APP_PASSWORD is not configured
    """
    if not _GMAIL_APP_PWD:
        raise RuntimeError("GMAIL_APP_PASSWORD is not configured")

    from_display = from_override or _FROM_EMAIL

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"]    = f"{_FROM_NAME} <{from_display}>"
    message["To"]      = to_email
    message.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
        server.starttls()
        server.login(_GMAIL_USER, _GMAIL_APP_PWD)
        server.sendmail(_FROM_EMAIL, [to_email], message.as_string())

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
# Kept in sync with backend/email_utils.py. If the template changes in the
# backend, update here too (Phase 5 will consolidate into a shared library).

def render_nomination_pending(
    manager_name: str,
    nominator_name: str,
    beneficiary_name: str,
    dollar_amount: float,
    currency: str,
    description: str,
    approve_url: str,
    reject_url: str,
) -> str:
    """Approver notification with Approve / Reject action buttons."""
    formatted_amount = _fmt(dollar_amount, currency)
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;
                 max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background-color: #f8f9fa; border-radius: 10px; padding: 30px; margin-bottom: 20px;">
            <h2 style="color: #2c3e50; margin-top: 0;">🔔 New Award Nomination Pending Approval</h2>
            <p style="font-size: 16px;">Dear <strong>{manager_name}</strong>,</p>
            <p style="font-size: 16px;">
                <strong>{nominator_name}</strong> has nominated <strong>{beneficiary_name}</strong>
                for a monetary award of <strong>{formatted_amount}</strong>.
            </p>
        </div>

        <div style="background-color: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
                    padding: 20px; margin-bottom: 30px;">
            <h3 style="color: #2c3e50; margin-top: 0;">📝 Nomination Details:</h3>
            <p style="background-color: #f8f9fa; padding: 15px; border-radius: 5px;
                      border-left: 4px solid #3498db;">
                {description}
            </p>
        </div>

        <div style="text-align: center; margin: 40px 0;">
            <p style="font-size: 16px; margin-bottom: 20px;"><strong>Take Action:</strong></p>
            <a href="{approve_url}"
               style="display: inline-block; background-color: #27ae60; color: white;
                      padding: 15px 40px; text-decoration: none; border-radius: 5px;
                      font-weight: bold; font-size: 16px; margin: 0 10px 10px 0;
                      box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                ✅ Approve
            </a>
            <a href="{reject_url}"
               style="display: inline-block; background-color: #e74c3c; color: white;
                      padding: 15px 40px; text-decoration: none; border-radius: 5px;
                      font-weight: bold; font-size: 16px; margin: 0 0 10px 10px;
                      box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                ❌ Reject
            </a>
        </div>

        <div style="background-color: #fff3cd; border-left: 4px solid #ffc107;
                    padding: 15px; margin: 20px 0; border-radius: 4px;">
            <p style="margin: 0; font-size: 14px;">
                <strong>⏰ Note:</strong> This approval link will expire in 72 hours.
                You can also approve or reject this nomination by logging into the
                Award Nomination System.
            </p>
        </div>

        <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 30px 0;">
        <p style="color: #7f8c8d; font-size: 12px; text-align: center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """


def render_nomination_approved(beneficiary_name: str, dollar_amount: float, currency: str) -> str:
    """Nominator notification — their nomination was approved."""
    formatted_amount = _fmt(dollar_amount, currency)
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #27ae60;">🎉 Nomination Approved!</h2>
        <p>Great news! Your nomination has been approved:</p>
        <ul>
            <li><strong>Nominee:</strong> {beneficiary_name}</li>
            <li><strong>Award:</strong> Monetary Award ({formatted_amount})</li>
        </ul>
        <p>The nominee will be notified of this honour.</p>
        <hr style="margin: 20px 0;">
        <p style="color: #7f8c8d; font-size: 12px;">
            This is an automated message from the Award Nomination System.
        </p>
    </body>
    </html>
    """


def render_payment_confirmed(
    beneficiary_name: str,
    dollar_amount: float,
    currency: str,
    payment_ref: str,
) -> str:
    """Nominator notification — payment for their approved nomination has been processed."""
    formatted_amount = _fmt(dollar_amount, currency)
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #2980b9;">💳 Payment Processed</h2>
        <p>The monetary award for your approved nomination has been paid:</p>
        <ul>
            <li><strong>Nominee:</strong> {beneficiary_name}</li>
            <li><strong>Amount:</strong> {formatted_amount}</li>
            <li><strong>Payment Reference:</strong> {payment_ref}</li>
        </ul>
        <p>The payment has been submitted to payroll and will appear on the next pay run.</p>
        <hr style="margin: 20px 0;">
        <p style="color: #7f8c8d; font-size: 12px;">
            This is an automated message from the Award Nomination System.
        </p>
    </body>
    </html>
    """


def render_nomination_rejected(beneficiary_name: str, dollar_amount: float, currency: str) -> str:
    """Nominator notification — their nomination was rejected."""
    formatted_amount = _fmt(dollar_amount, currency)
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #e74c3c;">Nomination Status Update</h2>
        <p>Your nomination has been reviewed:</p>
        <ul>
            <li><strong>Nominee:</strong> {beneficiary_name}</li>
            <li><strong>Award:</strong> Monetary Award ({formatted_amount})</li>
            <li><strong>Outcome:</strong> Not approved at this time</li>
        </ul>
        <p>
            Thank you for recognising your colleague. You are encouraged to
            continue nominating outstanding contributors.
        </p>
        <hr style="margin: 20px 0;">
        <p style="color: #7f8c8d; font-size: 12px;">
            This is an automated message from the Award Nomination System.
        </p>
    </body>
    </html>
    """
