"""
webhook_router.py — Gusto webhook receiver
==========================================
Gusto POSTs to this endpoint when a payroll event occurs.

Security: every request is validated by GustoProvider.validate_webhook()
which checks the X-Gusto-Signature HMAC-SHA256 header before any payload
processing.

Handled events:
  payroll.submitted → payroll confirmed by Gusto
                    → delegates to webhook_utils.handle_payroll_confirmed()
                    → publishes payroll.accepted to Service Bus

All other events are acknowledged (200) but not acted on.  Gusto retries
on non-200 responses, so we always return 200 even for unhandled events.
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Response

from providers.registry import PROVIDER_REGISTRY
from utils.webhook_utils import handle_payroll_confirmed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gusto", tags=["gusto-webhook"])

_provider = PROVIDER_REGISTRY["gusto"]
_PAYROLL_SUBMITTED_EVENT = "payroll.submitted"


@router.post("/webhook")
async def gusto_webhook(request: Request):
    """
    Receive and process Gusto webhook callbacks.

    AFD routes POST https://payroll-broker.terianix.ai/gusto/webhook here.
    Configure this URL + the matching GUSTO_WEBHOOK_SECRET in the Gusto
    developer portal.
    """
    # 1. Read raw body (must happen before JSON parsing for signature check)
    raw_body = await request.body()

    # 2. Validate signature via provider
    if not _provider.validate_webhook(raw_body, dict(request.headers)):
        sig = request.headers.get("X-Gusto-Signature", "<missing>")
        logger.warning("Gusto webhook signature invalid sig_header=%s", sig)
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    # 3. Parse payload
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Gusto webhook body: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type  = payload.get("event_type", "")
    entity_type = payload.get("entity_type", "")
    entity_uuid = payload.get("entity_uuid", "")

    logger.info(
        "Gusto webhook event_type=%s entity_type=%s entity_uuid=%s",
        event_type, entity_type, entity_uuid,
    )

    # 4. Route to handler
    if event_type == _PAYROLL_SUBMITTED_EVENT:
        await handle_payroll_confirmed(entity_uuid)

    # Always 200 — Gusto retries on non-2xx
    return Response(status_code=200)
