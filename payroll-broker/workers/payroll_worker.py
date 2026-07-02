"""
payroll_worker.py — Service Bus consumer for payroll processing
===============================================================
Generic background task that consumes nomination.approved events and
dispatches each one to the correct payroll provider via the registry.

No provider-specific code lives here.  The registry lookup on
provider_row.name determines which PayrollProvider implementation runs.

Flow per message:
  1. Decode body → {event_type, nomination_id}
  2. Fetch nomination (amount, currency, beneficiary email, tenant_id)
  3. Resolve provider via Tenants.payroll_provider_id → payroll_providers
  4. Look up provider object in PROVIDER_REGISTRY
  5. Call provider.get_credentials() — handles token refresh internally
  6. Call provider.find_employee()
  7. Upsert payroll_submissions status='submitted'  (pre-submission record)
  8. Call provider.submit_payroll() → external ref
     a. On rejection: upsert status='rejected', reason=error → raise → step 10
     b. On acceptance: upsert status='accepted', completed_at=utcnow()
  9. Publish payroll.accepted → auxiliary-service marks nomination Paid
 10. On failure: upsert status='rejected', publish payroll.failed, dead-letter

Environment variables:
    SERVICE_BUS_FQNS
    SERVICE_BUS_TOPIC_NAME
    SERVICE_BUS_SUBSCRIPTION_NAME   (default: payroll-processor)
    MI_CLIENT_ID
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from azure.identity.aio import DefaultAzureCredential
from azure.servicebus.aio import ServiceBusClient

import utils.sqlhelper as db
from providers.registry import PROVIDER_REGISTRY
from utils.service_bus_publisher import publish_event

logger = logging.getLogger(__name__)

_FQNS         = os.environ.get("SERVICE_BUS_FQNS", "")
_TOPIC        = os.environ.get("SERVICE_BUS_TOPIC_NAME", "")
_SUBSCRIPTION = os.environ.get("SERVICE_BUS_SUBSCRIPTION_NAME", "payroll-processor")


async def process_message(nomination_id: int) -> None:
    """
    Submit an approved nomination to the tenant's configured payroll provider.

    All provider-specific behaviour (token refresh, employee lookup, payroll
    API calls) is encapsulated in the PayrollProvider implementation selected
    from PROVIDER_REGISTRY.  This function only orchestrates.

    Raises on any unrecoverable error — the caller dead-letters the message.
    """
    # 1. Fetch nomination
    nomination = db.get_nomination_for_payroll(nomination_id)
    if not nomination:
        raise ValueError(f"Nomination {nomination_id} not found in database")

    tenant_id       = nomination["tenant_id"]
    amount          = nomination["amount"]
    currency        = nomination["currency"]
    beneficiary_upn = nomination["beneficiary_upn"]

    logger.info(
        "Processing payroll nomination_id=%d tenant_id=%d amount=%.2f %s beneficiary=%s",
        nomination_id, tenant_id, amount, currency, beneficiary_upn,
        extra={"nomination_id": nomination_id},
    )

    # 2. Resolve provider row
    provider_row = db.get_provider_for_tenant(tenant_id)
    if not provider_row:
        raise RuntimeError(
            f"No payroll provider configured for tenant_id={tenant_id}. "
            "An administrator must link a payroll_providers row to the tenant."
        )

    company_ref = provider_row.company_id_at_provider
    if not company_ref:
        raise RuntimeError(
            f"company_id_at_provider not set for provider_id={provider_row.id} "
            f"(tenant_id={tenant_id}). The tenant must complete the OAuth flow first."
        )

    # 3. Look up provider implementation
    provider = PROVIDER_REGISTRY.get(provider_row.name)
    if not provider:
        raise RuntimeError(
            f"Unknown payroll provider '{provider_row.name}' "
            f"(provider_id={provider_row.id}). "
            f"Registered providers: {list(PROVIDER_REGISTRY)}"
        )

    # 4. Get credentials (provider handles token refresh internally)
    token_row   = db.get_payroll_token_by_provider_id(provider_row.id)
    credentials = provider.get_credentials(provider_row, token_row)

    # 5. Find employee
    employee = provider.find_employee(credentials, company_ref, beneficiary_upn)

    # 6. Record pre-submission row so failures are always traceable
    db.upsert_payroll_submission(
        nomination_id=nomination_id,
        provider_id=provider_row.id,
        status="submitted",
    )

    # 7. Submit payroll to the provider
    try:
        payroll_ref = provider.submit_payroll(
            credentials=credentials,
            company_ref=company_ref,
            employee_id=employee["employee_id"],
            job_id=employee.get("job_id"),
            amount=amount,
            currency=currency,
        )
    except Exception as exc:
        db.upsert_payroll_submission(
            nomination_id=nomination_id,
            provider_id=provider_row.id,
            status="rejected",
            reason=str(exc)[:1000],
        )
        raise

    # 8. Provider accepted — stamp completed_at and record the ref
    db.upsert_payroll_submission(
        nomination_id=nomination_id,
        provider_id=provider_row.id,
        status="accepted",
        provider_payroll_ref=payroll_ref,
        completed_at=datetime.now(timezone.utc),
    )

    logger.info(
        "Payroll submitted nomination_id=%d provider=%s provider_id=%d ref=%s",
        nomination_id, provider_row.name, provider_row.id, payroll_ref,
        extra={"nomination_id": nomination_id},
    )

    # 9. Publish payroll.accepted so auxiliary-service marks the nomination Paid
    await publish_event(
        "payroll.accepted",
        nomination_id=nomination_id,
        extra={"payroll_ref": payroll_ref},
    )


async def run_worker(stop_event: asyncio.Event) -> None:
    """
    Long-running Service Bus consumer loop.

    Processes messages one at a time (max_message_count=1) until stop_event
    is set at application shutdown.

    On unrecoverable errors the message is dead-lettered and a payroll.failed
    event is published so Auxiliary Services can notify the approver and mark
    the nomination accordingly.
    """
    if not _FQNS or not _TOPIC:
        logger.warning(
            "SERVICE_BUS_FQNS or SERVICE_BUS_TOPIC_NAME not set — worker disabled"
        )
        return

    # DefaultAzureCredential automatically picks up AZURE_CLIENT_ID set by Terraform,
    # which disambiguates the user-assigned MI when multiple identities are attached.
    credential = None
    try:
        credential = DefaultAzureCredential()
        logger.info("Payroll worker starting topic=%s subscription=%s", _TOPIC, _SUBSCRIPTION)

        async with ServiceBusClient(_FQNS, credential) as sb_client:
            receiver = sb_client.get_subscription_receiver(
                topic_name=_TOPIC,
                subscription_name=_SUBSCRIPTION,
            )
            async with receiver:
                while not stop_event.is_set():
                    messages = await receiver.receive_messages(
                        max_message_count=1,
                        max_wait_time=5,
                    )

                    if not messages:
                        continue

                    msg = messages[0]

                    # Decode
                    try:
                        raw  = b"".join(msg.body)
                        body = json.loads(raw.decode("utf-8"))
                    except Exception as exc:
                        logger.error("Failed to decode SB message: %s", exc)
                        await receiver.dead_letter_message(
                            msg, reason="DecodeFailed",
                            error_description=str(exc),
                        )
                        continue

                    event_type    = body.get("event_type")
                    nomination_id = body.get("nomination_id")

                    if event_type != "nomination.approved":
                        logger.debug("Worker skipping event_type=%s", event_type)
                        await receiver.complete_message(msg)
                        continue

                    if not nomination_id:
                        logger.error("nomination.approved missing nomination_id")
                        await receiver.dead_letter_message(
                            msg, reason="MissingNominationId",
                            error_description="nomination_id absent in payload",
                        )
                        continue

                    try:
                        await process_message(nomination_id)
                        await receiver.complete_message(msg)
                        logger.info(
                            "SB message completed nomination_id=%d", nomination_id,
                            extra={"nomination_id": nomination_id},
                        )

                    except Exception as exc:
                        logger.exception(
                            "Payroll failed nomination_id=%d: %s", nomination_id, exc,
                            extra={"nomination_id": nomination_id},
                        )
                        try:
                            await publish_event(
                                "payroll.failed",
                                nomination_id=nomination_id,
                                extra={"error": str(exc)},
                            )
                        except Exception:
                            logger.exception(
                                "Failed to publish payroll.failed nomination_id=%d",
                                nomination_id,
                            )
                        await receiver.dead_letter_message(
                            msg, reason="PayrollSubmissionFailed",
                            error_description=str(exc)[:1024],
                        )

    except asyncio.CancelledError:
        logger.info("Payroll worker cancelled — shutting down")
    except Exception:
        logger.exception("Payroll worker crashed unexpectedly")
    finally:
        if credential:
            await credential.close()
        logger.info("Payroll worker stopped")
