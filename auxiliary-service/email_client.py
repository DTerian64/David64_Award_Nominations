"""
Email client for the auxiliary worker.

Synchronous wrapper around smtplib — no async needed in a worker process.
Uses Gmail SMTP. The HTML templates are defined below; the backend no longer
renders emails — they are sent asynchronously by this worker.
"""

import logging
import os
import smtplib
from email.mime.application import MIMEApplication
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


def send_email(
    to_email: str,
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    """
    Send an HTML email via Gmail SMTP.

    Args:
        attachments: optional list of (filename, data, content_type) tuples to
                     attach (e.g. the award certificate PDF). When omitted the
                     message is a plain HTML email as before.

    Raises:
        smtplib.SMTPException: on SMTP-level failure (caller decides retry strategy)
        RuntimeError: if GMAIL_APP_PASSWORD is not configured
    """
    if not _GMAIL_APP_PWD:
        raise RuntimeError("GMAIL_APP_PASSWORD is not configured")

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
            server.login(_GMAIL_USER, _GMAIL_APP_PWD)
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

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
            server.starttls()
            server.login(_GMAIL_USER, _GMAIL_APP_PWD)
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

def render_nomination_pending(
    manager_name: str,
    nominator_name: str,
    beneficiary_name: str,
    dollar_amount: float,
    currency: str,
    description: str,
    approve_url: str,
    reject_url: str,
    category: str | None = None,
) -> str:
    """Approver notification with Approve / Reject action buttons."""
    formatted_amount = _fmt(dollar_amount, currency)
    category_html = (
        f'<p style="margin: 4px 0 0;"><strong>Category:</strong> {category}</p>'
        if category else ""
    )
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
            {category_html}
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


def render_nomination_approved(
    beneficiary_name: str,
    dollar_amount: float,
    currency: str,
    category: str | None = None,
) -> str:
    """Nominator notification — their nomination was approved."""
    formatted_amount = _fmt(dollar_amount, currency)
    category_item = f"<li><strong>Category:</strong> {category}</li>" if category else ""
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #27ae60;">🎉 Nomination Approved!</h2>
        <p>Great news! Your nomination has been approved:</p>
        <ul>
            <li><strong>Nominee:</strong> {beneficiary_name}</li>
            <li><strong>Award:</strong> Monetary Award ({formatted_amount})</li>
            {category_item}
        </ul>
        <p>The nominee will be notified of this honour.</p>
        <hr style="margin: 20px 0;">
        <p style="color: #7f8c8d; font-size: 12px;">
            This is an automated message from the Award Nomination System.
        </p>
    </body>
    </html>
    """


def render_beneficiary_award(
    beneficiary_name: str,
    dollar_amount: float,
    currency: str,
    nominator_name: str | None = None,
    category: str | None = None,
) -> str:
    """Beneficiary notification — they have received a monetary award.

    Sent only when a nomination is approved (or paid). Shows the award amount.
    """
    formatted_amount = _fmt(dollar_amount, currency)
    nominator_html = (
        f'<li><strong>Recognised by:</strong> {nominator_name}</li>'
        if nominator_name else ""
    )
    category_item = f"<li><strong>Category:</strong> {category}</li>" if category else ""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    </head>
    <body style="font-family:Arial,sans-serif;line-height:1.6;color:#333;
                 max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f0fff4;border-radius:10px;padding:30px;margin-bottom:20px;
                    border:1px solid #c6f6d5;">
            <h2 style="color:#27ae60;margin-top:0;">🏆 Congratulations, {beneficiary_name}!</h2>
            <p style="font-size:16px;">
                You have been recognised with a monetary award of
                <strong>{formatted_amount}</strong> for your outstanding contribution.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">🎁 Award Details</h3>
            <ul style="padding-left:20px;">
                <li><strong>Award:</strong> Monetary Award ({formatted_amount})</li>
                {category_item}
                {nominator_html}
            </ul>
        </div>

        <p style="font-size:15px;">
            Thank you for the great work that earned this recognition. Your
            manager will be in touch and may present you with an award certificate.
        </p>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
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


def render_demo_access_invite(first_name: str, redeem_url: str) -> str:
    """Branded demo access invitation email sent to the self-registration requestor."""
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    </head>
    <body style="margin:0;padding:0;background-color:#f3f4f6;font-family:Arial,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background-color:#f3f4f6;padding:40px 0;">
        <tr><td align="center">
          <table width="580" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border-radius:12px;overflow:hidden;
                        box-shadow:0 2px 8px rgba(0,0,0,.08);max-width:580px;">

            <!-- Header -->
            <tr>
              <td style="background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);
                         padding:36px 40px;text-align:center;">
                <div style="font-size:48px;margin-bottom:12px;">🏆</div>
                <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;
                           letter-spacing:-0.3px;">
                  Award Nominations
                </h1>
                <p style="margin:6px 0 0;color:#c7d2fe;font-size:14px;">
                  Demo Environment
                </p>
              </td>
            </tr>

            <!-- Body -->
            <tr>
              <td style="padding:36px 40px;">
                <p style="margin:0 0 16px;font-size:16px;color:#111827;font-weight:600;">
                  Hi {first_name},
                </p>
                <p style="margin:0 0 16px;font-size:15px;color:#374151;line-height:1.7;">
                  Your demo access request has been approved! You're about to explore
                  a <strong>live SaaS platform</strong> for employee recognition and
                  monetary award management.
                </p>
                <p style="margin:0 0 28px;font-size:15px;color:#374151;line-height:1.7;">
                  Click the button below to activate your account. You'll be guided
                  through sign-in automatically — no password setup required.
                </p>

                <!-- CTA -->
                <table cellpadding="0" cellspacing="0" style="margin:0 auto 36px;">
                  <tr>
                    <td style="background:#4f46e5;border-radius:8px;
                               box-shadow:0 4px 12px rgba(79,70,229,.35);">
                      <a href="{redeem_url}"
                         style="display:inline-block;padding:16px 40px;
                                color:#ffffff;font-size:16px;font-weight:700;
                                text-decoration:none;letter-spacing:0.2px;">
                        Activate My Demo Access &rarr;
                      </a>
                    </td>
                  </tr>
                </table>

                <!-- Feature tiles -->
                <table cellpadding="0" cellspacing="0" width="100%"
                       style="border:1px solid #e5e7eb;border-radius:10px;
                              overflow:hidden;margin-bottom:28px;">
                  <tr>
                    <td style="background:#f5f3ff;padding:18px 24px;
                               border-bottom:1px solid #e5e7eb;">
                      <p style="margin:0;font-size:13px;font-weight:700;
                                color:#6d28d9;text-transform:uppercase;
                                letter-spacing:0.5px;">
                        What's included in your demo
                      </p>
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:6px 0;">
                      <table cellpadding="0" cellspacing="0" width="100%">
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            Submit and approve award nominations
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;
                                     background:#fafafa;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            Real-time analytics and spending trends
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            AI-powered fraud detection engine
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;
                                     background:#fafafa;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            Impersonate any demo user to explore different roles
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>

                <!-- Expiry note -->
                <div style="background:#fffbeb;border-left:4px solid #f59e0b;
                            border-radius:4px;padding:14px 18px;">
                  <p style="margin:0;font-size:13px;color:#92400e;">
                    <strong>⏰ Note:</strong> This invitation link expires in 30 days.
                    If you didn't request demo access, you can safely ignore this email.
                  </p>
                </div>
              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td style="padding:20px 40px;border-top:1px solid #e5e7eb;
                         text-align:center;background:#f9fafb;">
                <p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.6;">
                  This is an automated message from the Award Nominations demo environment.<br>
                  Please do not reply to this email.
                </p>
              </td>
            </tr>

          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """


# ── HRBP review workflow templates ───────────────────────────────────────────

_RISK_COLORS: dict[str, str] = {
    "CRITICAL": "#c0392b",
    "HIGH":     "#e67e22",
    "MEDIUM":   "#f39c12",
    "LOW":      "#27ae60",
    "NONE":     "#27ae60",
    "UNKNOWN":  "#7f8c8d",
}

def render_hrbp_review_request(
    hrbp_name: str,
    nomination_id: int,
    nominator_name: str,
    beneficiary_name: str,
    amount: float,
    currency: str,
    description: str,
    risk_level: str,
    fraud_score: float | None,
    warning_flags: list[str],
    portal_url: str | None = None,
) -> str:
    """HRBP notification — a nomination has been flagged and needs HR review."""
    formatted_amount = _fmt(amount, currency)
    risk_color = _RISK_COLORS.get(risk_level.upper(), "#7f8c8d")
    score_html = (
        f"<li><strong>Fraud Score:</strong> {fraud_score:.3f}</li>"
        if fraud_score is not None else ""
    )
    flags_html = ""
    if warning_flags:
        flags_list = "".join(f"<li>{f}</li>" for f in warning_flags)
        flags_html = f"""
        <div style="background:#fff3cd;border-left:4px solid #f39c12;
                    padding:12px 16px;border-radius:4px;margin:16px 0;">
            <strong>⚠️ Warning Flags:</strong>
            <ul style="margin:8px 0 0;padding-left:20px;">{flags_list}</ul>
        </div>"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    </head>
    <body style="font-family:Arial,sans-serif;line-height:1.6;color:#333;
                 max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#2c3e50;margin-top:0;">🔍 HRBP Review Required</h2>
            <p style="font-size:16px;">Dear <strong>{hrbp_name}</strong>,</p>
            <p style="font-size:16px;">
                The fraud detection system has flagged nomination
                <strong>#{nomination_id}</strong> for your review before it proceeds
                to manager approval.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">📋 Nomination Details</h3>
            <ul style="padding-left:20px;">
                <li><strong>Nominator:</strong> {nominator_name}</li>
                <li><strong>Nominee:</strong> {beneficiary_name}</li>
                <li><strong>Amount:</strong> {formatted_amount}</li>
            </ul>
            <p style="background:#f8f9fa;padding:12px;border-radius:5px;
                      border-left:4px solid #3498db;margin:0;">{description}</p>
        </div>

        <div style="background:#fff;border:2px solid {risk_color};border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:{risk_color};margin-top:0;">⚠️ Risk Assessment</h3>
            <ul style="padding-left:20px;">
                <li><strong>Risk Level:</strong>
                    <span style="color:{risk_color};font-weight:bold;">{risk_level}</span>
                </li>
                {score_html}
            </ul>
            {flags_html}
        </div>

        <div style="background:#e8f4fd;border-left:4px solid #3498db;
                    padding:15px;border-radius:4px;margin-bottom:20px;">
            <p style="margin:0;font-size:14px;">
                <strong>Action required:</strong> Please log into the
                {'<a href="' + portal_url + '" style="color:#2980b9;font-weight:bold;">Award Nominations portal</a>' if portal_url else '<strong>Award Nominations portal</strong>'}
                to review the full nomination details and either approve or
                reject the nomination. The nominator will be notified of your decision.
            </p>
        </div>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """


def render_hrbp_approved(
    nominator_name: str,
    beneficiary_name: str,
    amount: float,
    currency: str,
) -> str:
    """Nominator notification — their nomination has cleared HRBP review."""
    formatted_amount = _fmt(amount, currency)
    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#27ae60;margin-top:0;">✅ Nomination Cleared HR Review</h2>
            <p style="font-size:16px;">Dear <strong>{nominator_name}</strong>,</p>
            <p style="font-size:16px;">
                Your nomination for <strong>{beneficiary_name}</strong>
                ({formatted_amount}) has been reviewed and approved by the HR team.
            </p>
        </div>
        <p style="font-size:15px;">
            Your nomination has now been forwarded to the relevant manager for
            final approval. You will receive another notification once the manager
            has made their decision.
        </p>
        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """


def render_hrbp_rejected(
    nominator_name: str,
    beneficiary_name: str,
    amount: float,
    currency: str,
    reason: str,
) -> str:
    """Nominator notification — their nomination was rejected at the HRBP review stage."""
    formatted_amount = _fmt(amount, currency)
    reason_html = (
        f"""<div style="background:#f8f9fa;padding:12px;border-radius:5px;
                        border-left:4px solid #e74c3c;margin:16px 0;">
                <strong>Reason provided:</strong><br>{reason}
            </div>"""
        if reason else ""
    )
    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#e74c3c;margin-top:0;">Nomination Not Approved</h2>
            <p style="font-size:16px;">Dear <strong>{nominator_name}</strong>,</p>
            <p style="font-size:16px;">
                Your nomination for <strong>{beneficiary_name}</strong>
                ({formatted_amount}) was reviewed by the HR team and has not
                been approved to proceed at this time.
            </p>
        </div>
        {reason_html}
        <p style="font-size:15px;">
            Thank you for recognising your colleague. You are encouraged to
            continue nominating outstanding contributors.
        </p>
        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """


def render_hrbp_sla_breach(
    recipient_name: str,
    nomination_id: int,
    nominator_name: str,
    beneficiary_name: str,
    risk_level: str,
    nomination_date: str,
    sla_hours: int,
) -> str:
    """HRBP alert — a nomination has exceeded its review SLA."""
    risk_color = _RISK_COLORS.get(risk_level.upper(), "#7f8c8d")
    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#fdf2f2;border:2px solid #e74c3c;border-radius:10px;
                    padding:30px;margin-bottom:20px;">
            <h2 style="color:#c0392b;margin-top:0;">🚨 SLA Breach — HRBP Review Overdue</h2>
            <p style="font-size:16px;">Dear <strong>{recipient_name}</strong>,</p>
            <p style="font-size:16px;">
                Nomination <strong>#{nomination_id}</strong> has been awaiting HRBP
                review for more than <strong>{sla_hours} hours</strong> and requires
                your immediate attention.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">📋 Nomination Details</h3>
            <ul style="padding-left:20px;">
                <li><strong>Nomination ID:</strong> #{nomination_id}</li>
                <li><strong>Nominator:</strong> {nominator_name}</li>
                <li><strong>Nominee:</strong> {beneficiary_name}</li>
                <li><strong>Submitted:</strong> {nomination_date}</li>
                <li><strong>Risk Level:</strong>
                    <span style="color:{risk_color};font-weight:bold;">{risk_level}</span>
                </li>
            </ul>
        </div>

        <div style="background:#fff3cd;border-left:4px solid #ffc107;
                    padding:15px;border-radius:4px;margin-bottom:20px;">
            <p style="margin:0;font-size:14px;">
                <strong>⏰ Action required:</strong> Please log into the Award Nominations
                portal and review this nomination as soon as possible to avoid
                further escalation.
            </p>
        </div>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """


def render_description_rejected(
    nominator_name:        str,
    beneficiary_name:      str,
    amount:                float,
    currency:              str,
    check:                 str,
    reason:                str,
    category_description:  str | None = None,
    nomination_description: str | None = None,
) -> str:
    """
    Nominator notification — nomination auto-rejected by the description
    quality pipeline (Check A: category alignment).

    'reason' is the human-readable explanation from description_check.py,
    already including a resubmission suggestion.
    """
    formatted_amount = _fmt(amount, currency)
    category_item = (
        f"<li><strong>Category:</strong> {category_description}</li>"
        if category_description else ""
    )
    check_label = {
        "category_alignment": "Description does not match category",
    }.get(check, "Description quality check failed")

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#e67e22;margin-top:0;">Nomination Requires a Better Description</h2>
            <p style="font-size:16px;">Dear <strong>{nominator_name}</strong>,</p>
            <p style="font-size:16px;">
                Your nomination for <strong>{beneficiary_name}</strong>
                ({formatted_amount}) was not accepted because the description
                did not meet our quality requirements.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">📋 Nomination Details</h3>
            <ul style="padding-left:20px;">
                <li><strong>Nominee:</strong> {beneficiary_name}</li>
                <li><strong>Award Amount:</strong> {formatted_amount}</li>
                {category_item}
            </ul>
        </div>

        {f'''<div style="background:#f4f6f8;border:1px solid #d0d7de;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">Your Original Description</h3>
            <p style="font-size:14px;white-space:pre-wrap;margin:0;">{nomination_description}</p>
        </div>''' if nomination_description else ''}

        <div style="background:#fff8f0;border-left:4px solid #e67e22;
                    padding:15px;border-radius:4px;margin-bottom:20px;">
            <p style="margin:0 0 8px 0;font-size:14px;">
                <strong>⚠ Issue: {check_label}</strong>
            </p>
            <p style="margin:0;font-size:14px;">{reason}</p>
        </div>

        <p style="font-size:15px;">
            You are welcome to resubmit this nomination with an improved description.
            A good description explains <em>what</em> the person did and
            <em>why</em> it was impactful — specific examples make a strong case.
        </p>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """


def render_nomination_rejected(
    beneficiary_name: str,
    dollar_amount: float,
    currency: str,
    category: str | None = None,
) -> str:
    """Nominator notification — their nomination was rejected."""
    formatted_amount = _fmt(dollar_amount, currency)
    category_item = f"<li><strong>Category:</strong> {category}</li>" if category else ""
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #e74c3c;">Nomination Status Update</h2>
        <p>Your nomination has been reviewed:</p>
        <ul>
            <li><strong>Nominee:</strong> {beneficiary_name}</li>
            <li><strong>Award:</strong> Monetary Award ({formatted_amount})</li>
            {category_item}
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
