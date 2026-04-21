"""
agents/skills/notifications/tools.py
──────────────────────────────────────
Notification tools: email delivery and calendar scheduling.

Tools
─────
  send_email       — publishes a notification.requested event to Service Bus;
                     the auxiliary service picks it up and delivers via SMTP.
  add_to_calendar  — STUB: acknowledged + logged; full integration coming later.

Why Service Bus instead of direct SMTP?
  The backend (Ask Analytics) has no business owning SMTP credentials or
  managing delivery retries.  Publishing an event decouples the request from
  the delivery:
    - The auxiliary service already handles all email delivery.
    - If SMTP is slow or fails, the message stays in the bus and retries
      automatically without blocking the agent's response loop.
    - One place (auxiliary) owns all email logic — templates, retries, logging.

Environment variables required (already set on the backend container)
──────────────────────────────────────────────────────────────────────
  SERVICE_BUS_FQNS        e.g. sb-award-sandbox.servicebus.windows.net
  SERVICE_BUS_TOPIC_NAME  e.g. award-events
  MI_CLIENT_ID            user-assigned managed identity client ID
"""

from __future__ import annotations

import logging
import os

from service_bus_publisher import publish_event

logger = logging.getLogger(__name__)


# ── send_email ────────────────────────────────────────────────────────────────

async def send_email(to: str, subject: str, body: str) -> dict:
    """
    Request an email delivery by publishing a notification.requested event.

    The auxiliary service consumes the event and delivers the email via Gmail
    SMTP.  This function returns as soon as the message is on the bus — it
    does not wait for SMTP delivery.

    Payload published:
        {
            "event_type": "notification.requested",
            "to":         "recipient@company.com",
            "subject":    "Fraud Investigation Summary",
            "body":       "Plain-text body...",
            "from":       "system@terian-services.com"   ← FROM_EMAIL env var
        }
    """
    from_email = os.getenv("FROM_EMAIL", os.getenv("GMAIL_USER", ""))

    try:
        await publish_event(
            event_type    = "notification.requested",
            nomination_id = None,   # not tied to a nomination
            extra         = {
                "from":    from_email,
                "to":      to,
                "subject": subject,
                "body":    body,
            },
        )
        logger.info("send_email: notification.requested published → %s", to)
        return {
            "status":  "success",
            "message": f"Email queued for delivery to {to}.",
            "subject": subject,
        }
    except Exception as exc:
        logger.error("send_email: failed to publish event: %s", exc, exc_info=True)
        return {"status": "error", "message": str(exc)}


# ── add_to_calendar ───────────────────────────────────────────────────────────

async def add_to_calendar(
    title:          str,
    description:    str,
    start_datetime: str,
    end_datetime:   str | None = None,
) -> dict:
    """
    STUB — calendar integration is planned but not yet implemented.

    Accepts and logs the request; returns a not-implemented status so the
    agent can acknowledge the user gracefully.

    When implemented: publish a calendar.requested event and have the
    auxiliary service write to Google Calendar / Microsoft Graph via OAuth.
    """
    logger.info("add_to_calendar (STUB): %r @ %s", title, start_datetime)
    return {
        "status":  "not_implemented",
        "message": "Calendar integration is coming soon. Your request has been noted.",
        "requested": {
            "title":          title,
            "description":    description,
            "start_datetime": start_datetime,
            "end_datetime":   end_datetime,
        },
    }


# ── OpenAI tool schemas ───────────────────────────────────────────────────────

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Queue an email for delivery by publishing a notification event. "
                "Use when the user asks to email findings, notify a colleague, "
                "or share investigation results. "
                "Compose a professional body — include key signals, names, amounts, "
                "and recommended actions relevant to the recipient."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to":      {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Concise, descriptive subject line."},
                    "body":    {"type": "string", "description": "Plain-text email body."},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_calendar",
            "description": (
                "Log a calendar follow-up request. STUB — full calendar write coming soon. "
                "Use when the user asks to schedule a review, set a reminder, or block "
                "time for follow-up. Always confirm to the user that this is a stub."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title":          {"type": "string", "description": "Calendar event title."},
                    "description":    {"type": "string", "description": "Event description or agenda."},
                    "start_datetime": {"type": "string", "description": "ISO 8601 start datetime."},
                    "end_datetime":   {"type": "string", "description": "ISO 8601 end datetime (optional)."},
                },
                "required": ["title", "description", "start_datetime"],
            },
        },
    },
]

IMPLEMENTATIONS = {
    "send_email":      send_email,
    "add_to_calendar": add_to_calendar,
}
