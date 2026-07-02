"""
Handler: payroll.accepted
=========================
Triggered when the payroll broker successfully submits an off-cycle payroll to
the provider (e.g. Gusto accepts the request and returns a payroll UUID).

This handler's sole job is to move the nomination to "Paid" status so the
front-end reflects the completed payment.

Payload keys
------------
  event_type    "payroll.accepted"
  nomination_id int
  payroll_ref   str — provider-assigned payroll UUID (for audit trail)
"""

import logging

import db

logger = logging.getLogger("auxiliary.handlers.payroll_accepted")


def handle(payload: dict) -> None:
    nomination_id = payload.get("nomination_id")
    payroll_ref   = payload.get("payroll_ref", "")

    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    db.set_nomination_status(nomination_id, "Paid")

    logger.info(
        "Nomination marked Paid",
        extra={"nomination_id": nomination_id, "payroll_ref": payroll_ref},
    )
