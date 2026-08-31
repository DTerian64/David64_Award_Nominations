"""
webhook_router.py — Rippling webhook receiver
=============================================
Rippling POSTs to this endpoint when a payroll run event occurs.

Security: every request is validated by RipplingProvider.validate_webhook()
which checks the X-Rippling-Signature HMAC-SHA256 header before any payload
processing.  In stub mode validation always passes (no real webhooks arrive).

Handled events:
  payroll_run.completed → payroll run confirmed by Rippling
                        → delegates to webhook_utils.handle_payroll_confirmed()
                        → publishes payroll.accepted to Service Bus

All other events are acknowledged (200) but not acted on.
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Response

from providers.registry import PROVIDER_REGISTRY
from utils.webhook_utils import handle_payroll_confirmed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rippling", tags=["rippling-webhook"])

_provider = PROVIDER_REGISTRY["rippling"]
_PAYROLL_COMPLETED_EVENT = "payroll_run.completed"


@router.post("/webhook")
async def rippling_webhook(request: Request):
    """
    Receive and process Rippling webhook callbacks.

    AFD routes POST https://payroll-broker.terianix.ai/rippling/webhook here.
    Configure this URL + RIPPLING_WEBHOOK_SECRET in the Rippling developer portal.
    """
    # 1. Read raw body before JSON parsing (required for signature verification)
    raw_body = await request.body()

    # 2. Validate signature
    if not _provider.validate_webhook(raw_body, dict(request.headers)):
        sig = request.headers.get("X-Rippling-Signature", "<missing>")
        logger.warning("Rippling webhook signature invalid sig_header=%s", sig)
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    # 3. Parse payload
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Rippling webhook body: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Rippling webhook envelope:
    # { "event": "payroll_run.completed", "data": { "id": "<run_id>", ... } }
    event   = payload.get("event", "")
    data    = payload.get("data", {})
    run_id  = data.get("id", "")

    logger.info("Rippling webhook event=%s run_id=%s", event, run_id)

    # 4. Route to handler
    if event == _PAYROLL_COMPLETED_EVENT and run_id:
        await handle_payroll_confirmed(run_id)

    # Always 200 — Rippling retries on non-2xx
    return Response(status_code=200)
