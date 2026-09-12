"""One-message orchestration with explicit broker settlement semantics."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from extensions.gnn_explainer.artifacts import BundleLoader
from extensions.gnn_explainer.contracts import ExplanationRequest
from extensions.gnn_explainer.errors import PermanentExtensionError, TransientExtensionError
from extensions.gnn_explainer.reproduction import ReproductionPolicy, reproduce
from utils import db

logger = logging.getLogger("integrity_check_extension.gnn_explainer")


class Settlement(str, Enum):
    COMPLETE = "complete"
    ABANDON = "abandon"
    DEFER = "defer"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class DispatchResult:
    settlement: Settlement
    reason: str | None = None
    request: ExplanationRequest | None = None


def dispatch(
    payload: dict,
    delivery_count: int,
    loader: BundleLoader,
    on_claim: Callable[[], None] | None = None,
) -> DispatchResult:
    request = None
    claimed = False
    try:
        request = ExplanationRequest.parse(payload)
        context = db.load_request_context(request)
        claimed = db.claim_request(request, delivery_count)
        if not claimed:
            status = (context.gnn_result.get("explanation") or {}).get("status")
            if status == "COMPLETED":
                db.insert_nomination_log(
                    request, "INFO",
                    "GNN explanation request already completed — skipping",
                    {"request_id": request.request_id, "attempt": delivery_count},
                )
                return DispatchResult(Settlement.COMPLETE, "ALREADY_COMPLETED", request)
            # Do not abandon immediately: rapid redelivery would exhaust the
            # broker delivery count before the stale SQL lease can recover.
            return DispatchResult(Settlement.DEFER, "REQUEST_ALREADY_RUNNING", request)

        if on_claim is not None:
            on_claim()

        db.insert_nomination_log(request, "INFO", "GNN explanation started", {
            "request_id": request.request_id,
            "attempt": delivery_count,
            "model_version": request.model_version,
            "graph_snapshot_id": request.graph_snapshot_id,
        })
        bundle = loader.load(request)
        details = context.details
        embeddings = db.get_versioned_embeddings(
            request, [details["nominator_id"], details["beneficiary_id"]]
        )
        result = reproduce(
            bundle=bundle,
            details=details,
            gnn_result=context.gnn_result,
            sql_embeddings=embeddings,
            policy=ReproductionPolicy.from_configuration(context.policy_configuration),
        )
        # E1/E2 are intentionally deployed while tenant request production is
        # disabled. Fail closed if a request is nevertheless introduced; E3
        # replaces this boundary with GNNExplainer execution.
        raise PermanentExtensionError(
            "EXPLANATION_ENGINE_NOT_DEPLOYED:"
            f"reproduced={result.serving_probability:.8f}"
        )
    except TransientExtensionError as exc:
        if request and claimed:
            _persist_failure(request, "TRANSIENT_DEPENDENCY_FAILURE", str(exc), delivery_count)
        return DispatchResult(Settlement.ABANDON, str(exc), request)
    except PermanentExtensionError as exc:
        if request and claimed:
            reason = str(exc).split(":", 1)[0]
            _persist_failure(request, reason, str(exc), delivery_count)
        return DispatchResult(Settlement.DEAD_LETTER, str(exc), request)
    except Exception as exc:
        logger.exception("Unexpected GNN extension failure")
        if request and claimed:
            _persist_failure(request, "INTERNAL_ERROR", str(exc), delivery_count)
        return DispatchResult(Settlement.ABANDON, "INTERNAL_ERROR", request)


def _persist_failure(
    request: ExplanationRequest, reason: str, detail: str, delivery_count: int
) -> None:
    bounded = detail[:500]
    db.finish_request(request, {
        "method": "GNNEXPLAINER",
        "status": "FAILED",
        "request_id": request.request_id,
        "reason": reason,
        "detail": bounded,
        "attempt": delivery_count,
    })
    db.insert_nomination_log(request, "ERROR", "GNN explanation failed", {
        "request_id": request.request_id,
        "attempt": delivery_count,
        "reason": reason,
        "model_version": request.model_version,
        "graph_snapshot_id": request.graph_snapshot_id,
    }, exception=bounded)
