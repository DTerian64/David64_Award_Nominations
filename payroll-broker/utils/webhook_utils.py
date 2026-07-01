"""
webhook_utils.py — Shared webhook tail logic
============================================
All provider webhook routers call handle_payroll_confirmed() once they have
validated the request and identified the external payroll reference.  This
keeps the "what do we do after confirmation" logic in one place regardless
of which provider fired the callback.
"""

import logging

import utils.sqlhelper as db
from utils.service_bus_publisher import publish_event

logger = logging.getLogger(__name__)


async def handle_payroll_confirmed(provider_payroll_ref: str) -> None:
    """
    Common tail for any provider's "payroll confirmed" webhook event.

    Looks up the submission by provider_payroll_ref, marks it completed,
    and publishes payroll.accepted to Service Bus so Auxiliary Services can
    update Nomination.Status = 'Paid' and send the payment notification.

    Idempotent: if the submission is already completed (e.g. a Gusto retry),
    the function logs and returns without republishing.

    If provider_payroll_ref is unknown (e.g. a manually triggered payroll
    in the Gusto portal), the function logs and returns — no Service Bus
    event is published.
    """
    result = db.get_submission_by_payroll_ref(provider_payroll_ref)

    if result is None:
        logger.warning(
            "payroll_confirmed: no submission found for ref=%s — skipping",
            provider_payroll_ref,
        )
        return

    nomination_id, current_status = result

    if current_status == "completed":
        logger.info(
            "payroll_confirmed: already processed nomination_id=%d ref=%s — idempotent skip",
            nomination_id, provider_payroll_ref,
        )
        return

    # Mark completed in DB
    db.update_submission_status(provider_payroll_ref, "completed")

    # Publish payroll.accepted → Auxiliary Services picks this up
    try:
        await publish_event(
            "payroll.accepted",
            nomination_id=nomination_id,
            extra={"provider_payroll_ref": provider_payroll_ref},
        )
        logger.info(
            "payroll.accepted published nomination_id=%d ref=%s",
            nomination_id, provider_payroll_ref,
        )
    except Exception:
        logger.exception(
            "Failed to publish payroll.accepted nomination_id=%d ref=%s — "
            "DB already updated; reconciliation job can re-publish if needed",
            nomination_id, provider_payroll_ref,
        )
        # Do not re-raise: the 200 we return to the provider prevents a retry
        # that would double-publish.  The DB status='completed' is the source
        # of truth; a reconciliation job can detect the discrepancy.
