"""
Event dispatcher — routes incoming Service Bus messages to the correct handler
and enforces idempotency via dbo.ProcessedEvents.

Idempotency contract
--------------------
Before calling any handler, the dispatcher attempts to insert a row into
dbo.ProcessedEvents with the Service Bus MessageId as the primary key.

  INSERT succeeds → message has never been processed → call handler → record result
  INSERT fails (PK violation) → message already processed → skip silently

This guarantees at-most-once *side effects* (emails, DB updates) even though
Service Bus provides at-least-once *delivery*.

Handler contract
----------------
Each handler is a plain function: (payload: dict) -> None
  - Returns None on success.
  - Raises an exception on failure (dispatcher records 'error' and re-raises
    so main.py can abandon the message for retry).

Adding a new event type
-----------------------
1. Create handlers/my_event.py with a handle(payload) function.
2. Register it in HANDLERS below.
No other changes required.
"""

import logging
from datetime import datetime, timezone
from typing import Callable

import db
from handlers import nomination_created, nomination_approved, payout_submit, payout_accepted, notification_requested, access_requested

logger = logging.getLogger("auxiliary.dispatcher")

# ── Handler registry ──────────────────────────────────────────────────────────
# Each value is either a single callable or a list of callables executed in
# order.  All handlers in a list are called; if one raises, execution stops
# and the message is abandoned for retry.
HANDLERS: dict[str, Callable[[dict], None] | list[Callable[[dict], None]]] = {
    "nomination.created":      nomination_created.handle,
    # nomination.approved triggers both the outcome email AND the payout submission.
    "nomination.approved":     [nomination_approved.handle, payout_submit.handle],
    "payout.accepted":         payout_accepted.handle,
    # Free-form email delivery requested by the Ask Analytics agent (or any backend service).
    # Payload carries From / To / Subject / Body directly — no DB lookup needed.
    "notification.requested":  notification_requested.handle,
    # Branded HTML invitation email sent to a demo self-registration requestor.
    "notification.access_requested": access_requested.handle,
}


def dispatch(message_id: str, payload: dict) -> str:
    """
    Route a decoded Service Bus message to its handler.

    Args:
        message_id: Service Bus message ID (used as idempotency key)
        payload:    Decoded JSON body

    Returns:
        'success' | 'skipped'

    Raises:
        ValueError: Unknown event type or missing fields
        Exception:  Handler failure (caller should abandon the message)
    """
    event_type   = payload.get("event_type")
    nomination_id = payload.get("nomination_id")  # may be None for future event types

    if not event_type:
        raise ValueError(f"Missing 'event_type' in payload: {payload}")

    handler_entry = HANDLERS.get(event_type)
    if handler_entry is None:
        raise ValueError(f"No handler registered for event_type='{event_type}'")

    # Normalise to a list so single-handler and multi-handler events are
    # treated identically below.
    handlers: list[Callable[[dict], None]] = (
        handler_entry if isinstance(handler_entry, list) else [handler_entry]
    )

    # ── Idempotency check ─────────────────────────────────────────────────────
    # Try to claim the message. If already claimed → skip.
    already_processed = db.claim_message(
        message_id=message_id,
        event_type=event_type,
        nomination_id=nomination_id,
        processed_at=datetime.now(timezone.utc),
    )

    if already_processed:
        logger.warning(
            "Message already processed — skipping",
            extra={"message_id": message_id, "event_type": event_type}
        )
        return "skipped"

    # ── Handle ────────────────────────────────────────────────────────────────
    logger.info(
        "Dispatching event",
        extra={"event_type": event_type, "nomination_id": nomination_id,
               "handler_count": len(handlers)}
    )

    try:
        for fn in handlers:
            fn(payload)
        db.update_processed_event_result(message_id, "success")
        return "success"
    except Exception as exc:
        db.update_processed_event_result(message_id, "error", error=str(exc))
        raise  # re-raise so main.py abandons the message for retry
