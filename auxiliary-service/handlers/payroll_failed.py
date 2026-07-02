"""
Handler: payroll.failed
========================
Triggered when the payroll broker fails to submit an off-cycle payroll for an
approved nomination (e.g. Gusto rejects the request with 422 or 5xx).

The payroll_worker already dead-letters the Service Bus message and publishes
this event.  This handler's job is to notify the right people so the issue
can be investigated and the payroll manually reprocessed if necessary.

Recipient resolution
--------------------
1. All users with Role = 'Support' in dbo.UserRoles for the nomination's tenant.
2. If none are configured → CORPORATE_SUPPORT_EMAIL env var
   (default: support@terian-services.com).

Email template: 'payroll_failed' from dbo.EmailTemplates (seeded in migration 0031).

Payload keys
------------
  event_type    "payroll.failed"
  nomination_id int
  error         str — provider error message (from the caught exception)
"""

import logging
import os

import db
import email_client
import templating

logger = logging.getLogger("auxiliary.handlers.payroll_failed")

_CORPORATE_SUPPORT_EMAIL = os.getenv("CORPORATE_SUPPORT_EMAIL", "support@terian-services.com")


def handle(payload: dict) -> None:
    nomination_id = payload.get("nomination_id")
    error_msg     = payload.get("error", "Unknown error")

    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    # ── 1. Fetch nomination details ───────────────────────────────────────────
    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    tenant_id = details["tenant_id"]

    # ── 2. Resolve recipients ─────────────────────────────────────────────────
    support_users = db.get_support_users(tenant_id)

    if support_users:
        recipients = [u["email"] for u in support_users]
        logger.info(
            "Sending payroll failure notification to %d support user(s)",
            len(recipients),
            extra={"nomination_id": nomination_id, "tenant_id": tenant_id},
        )
    else:
        recipients = [_CORPORATE_SUPPORT_EMAIL]
        logger.warning(
            "No Support-role users configured for tenant — falling back to corporate support",
            extra={
                "nomination_id":  nomination_id,
                "tenant_id":      tenant_id,
                "fallback_email": _CORPORATE_SUPPORT_EMAIL,
            },
        )

    # ── 3. Render and send ────────────────────────────────────────────────────
    lang = db.get_tenant_lang(tenant_id)
    rendered = templating.render(
        tenant_id, "payroll_failed", lang,
        {
            "nomination_id":    nomination_id,
            "beneficiary_name": details["beneficiary_name"],
            "nominator_name":   details["nominator_name"],
            "formatted_amount": email_client.format_amount(
                details["amount"], details.get("currency", "USD")
            ),
            "error_message":    error_msg,
        },
    )

    for recipient in recipients:
        email_client.send_email(
            to_email=recipient,
            subject=rendered["subject"],
            body=rendered["body"],
        )
        logger.info(
            "Payroll failure email sent",
            extra={"nomination_id": nomination_id, "recipient": recipient},
        )

    logger.info(
        "payroll.failed handled",
        extra={"nomination_id": nomination_id, "recipient_count": len(recipients)},
    )
