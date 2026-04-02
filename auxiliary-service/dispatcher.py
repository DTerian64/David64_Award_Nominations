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
from handlers import nomination_created, nomination_approved

logger = logging.getLogger("auxiliary.dispatcher")

# ── Handler registry ──────────────────────────────────────────────────────────
HANDLERS: dict[str, Callable[[dict], None]] = {
    "nomination.created":  nomination_created.handle,
    "nomination.approved": nomination_approved.handle,
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

    handler = HANDLERS.get(event_type)
    if handler is None:
        raise ValueError(f"No handler registered for event_type='{event_type}'")

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
        extra={"event_type": event_type, "nomination_id": nomination_id}
    )

    try:
        handler(payload)
        db.update_processed_event_result(message_id, "success")
        return "success"
    except Exception as exc:
        db.update_processed_event_result(message_id, "error", error=str(exc))
        raise  # re-raise so main.py abandons the message for retry
