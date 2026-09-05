"""Independent nomination-time Graph Analytics opinion."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import date

from integrity_engine import (
    CandidateNomination,
    EvaluationLimitExceeded,
    GraphInferenceSnapshot,
    __version__ as integrity_engine_version,
    evaluate_ring_candidate,
)

from . import component_availability
from utils import db

logger = logging.getLogger("integrity_check.graph_check")

_snapshot_cache: OrderedDict[tuple[int, str, str], GraphInferenceSnapshot] = OrderedDict()
_snapshot_cache_lock = threading.Lock()


def _load_inference_snapshot(tenant_id: int, metadata: dict) -> GraphInferenceSnapshot:
    """Load and verify the immutable candidate-evaluation graph artifact."""
    blob_name = metadata.get("inference_snapshot_blob")
    digest = metadata.get("inference_snapshot_sha256")
    run_id = metadata.get("snapshot_run_id")
    if not all(isinstance(value, str) and value for value in (blob_name, digest, run_id)):
        raise db.InvalidGraphSnapshot("Graph candidate-evaluation artifact is missing")
    key = (tenant_id, run_id, digest)
    with _snapshot_cache_lock:
        cached = _snapshot_cache.get(key)
        if cached is not None:
            _snapshot_cache.move_to_end(key)
            return cached

    from azure.storage.blob import BlobServiceClient

    account = os.getenv("AZURE_STORAGE_ACCOUNT")
    container = os.getenv("MODEL_CONTAINER", "ml-models")
    if not account:
        raise db.InvalidGraphSnapshot("AZURE_STORAGE_ACCOUNT is not configured")
    storage_key = os.getenv("AZURE_STORAGE_KEY")
    if storage_key:
        client = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=storage_key,
        )
    else:
        from utils.azure_credential import credential
        client = BlobServiceClient(
            f"https://{account}.blob.core.windows.net", credential=credential
        )
    try:
        compressed = client.get_blob_client(
            container=container, blob=blob_name
        ).download_blob().readall()
    except Exception as exc:
        raise db.InvalidGraphSnapshot(
            f"Graph candidate-evaluation artifact could not be loaded: {blob_name}"
        ) from exc

    if hashlib.sha256(compressed).hexdigest() != digest:
        raise db.InvalidGraphSnapshot("Graph inference snapshot checksum mismatch")
    expected_size = metadata.get("inference_snapshot_size_bytes")
    if expected_size is not None and len(compressed) != int(expected_size):
        raise db.InvalidGraphSnapshot("Graph inference snapshot size mismatch")
    try:
        raw = gzip.decompress(compressed)
        if len(raw) > int(os.getenv("GRAPH_SNAPSHOT_MAX_BYTES", "104857600")):
            raise ValueError("Graph inference snapshot exceeds the configured size limit")
        snapshot = GraphInferenceSnapshot.from_dict(json.loads(raw))
    except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
        raise db.InvalidGraphSnapshot("Graph inference snapshot is malformed") from exc
    if (
        snapshot.tenant_id != tenant_id
        or snapshot.run_id != run_id
        or snapshot.policy_version != metadata.get("scoring_policy_version")
    ):
        raise db.InvalidGraphSnapshot("Graph inference snapshot provenance mismatch")

    with _snapshot_cache_lock:
        _snapshot_cache[key] = snapshot
        _snapshot_cache.move_to_end(key)
        maximum = max(1, int(os.getenv("GRAPH_SNAPSHOT_CACHE_SIZE", "8")))
        while len(_snapshot_cache) > maximum:
            _snapshot_cache.popitem(last=False)
    return snapshot


def _risk_level(score: float, thresholds: dict[str, float]) -> str:
    if score >= thresholds["critical"]:
        return "CRITICAL"
    if score >= thresholds["high"]:
        return "HIGH"
    if score >= thresholds["medium"]:
        return "MEDIUM"
    if score >= thresholds["low"]:
        return "LOW"
    return "NONE"


def _unavailable(
    reason: str,
    component_status: dict | None = None,
    *,
    source_missing: bool = False,
    snapshot_as_of=None,
    snapshot_run_id: str | None = None,
) -> dict:
    result = {
        "model_available": False,
        "fraud_score": 0,
        "fraud_prob": None,
        "risk_level": "NONE",
        "warning_flags": [],
        "flagged": False,
        "source_severity": None,
        "snapshot_as_of": snapshot_as_of,
        "snapshot_run_id": snapshot_run_id,
        "affected_user_ids": [],
        "pattern_findings": [],
        "candidate_findings": [],
        "nominator_history": [],
        "beneficiary_history": [],
        "shared_history": [],
        "winning_finding": None,
        "detector_summary": [],
        "winning_finding_hash": None,
        "winning_pattern_type": None,
        "winning_pattern_count": 0,
        "scoring_strategy": None,
        "scoring_policy_version": None,
    }
    result.update(component_availability.unavailable_metadata(
        "GRAPH", reason, component_status, source_missing=source_missing
    ))
    return result


def assess_graph(
    details: dict,
    tenant_id: int,
    component_status: dict | None = None,
) -> dict:
    """Return the current Graph opinion and emit its nomination audit trail.

    The nomination-scoped logging handler persists these records to
    dbo.Nomination_Logs.  Keeping the lifecycle here means Graph assessments
    remain auditable when this component is called outside the main handler.
    """
    nomination_id = details.get("nomination_id")
    logger.info(
        "Graph Analytics assessment starting",
        extra={"nomination_id": nomination_id, "tenant_id": tenant_id},
    )
    try:
        result = _assess_graph_inner(details, tenant_id, component_status)
    except Exception as exc:
        if isinstance(exc, db.InvalidGraphSnapshot):
            reason = "INVALID_SNAPSHOT"
        elif isinstance(exc, EvaluationLimitExceeded):
            reason = "EVALUATION_LIMIT"
        else:
            reason = "INFERENCE_FAILED"
        logger.error(
            "Graph Analytics assessment failed",
            extra={
                "nomination_id": nomination_id,
                "tenant_id": tenant_id,
                "unavailable_reason": reason,
                "error": str(exc),
            },
            exc_info=True,
        )
        result = _unavailable(reason, component_status)
        result['unavailable_detail'] = str(exc)

    logger.info(
        "Graph Analytics assessment completed",
        extra={
            "nomination_id": nomination_id,
            "tenant_id": tenant_id,
            "model_available": result["model_available"],
            "unavailable_reason": result.get("unavailable_reason"),
            "unavailable_detail": result.get("unavailable_detail"),
            "last_attempt_status": result.get("last_attempt_status"),
            "fraud_score": result.get("fraud_score"),
            "risk_level": result.get("risk_level"),
            "flagged": result.get("flagged", False),
            "warning_flags": result.get("warning_flags") or [],
            "winning_finding": result.get("winning_finding"),
            "detector_summary": result.get("detector_summary") or [],
            "pattern_findings": result.get("pattern_findings") or [],
            "candidate_findings": result.get("candidate_findings") or [],
            "snapshot_as_of": result.get("snapshot_as_of"),
            "snapshot_run_id": result.get("snapshot_run_id"),
            "snapshot_age_days": result.get("snapshot_age_days"),
            "scoring_strategy": result.get("scoring_strategy"),
            "scoring_policy_version": result.get("scoring_policy_version"),
            "winning_finding_hash": result.get("winning_finding_hash"),
            "winning_pattern_type": result.get("winning_pattern_type"),
            "winning_pattern_count": result.get("winning_pattern_count", 0),
            "finding_count": len(result.get("pattern_findings") or []),
            "candidate_evaluation_ms": result.get("candidate_evaluation_ms"),
        },
    )
    return result


def _assess_graph_inner(
    details: dict,
    tenant_id: int,
    component_status: dict | None = None,
) -> dict:
    roles = [
        (int(details["nominator_id"]), "nominator"),
        (int(details["beneficiary_id"]), "beneficiary"),
    ]
    snapshot = db.get_graph_component_snapshot(
        tenant_id,
        [user_id for user_id, _role in roles],
        component_status,
    )
    if snapshot is None:
        return _unavailable("NO_SNAPSHOT", component_status, source_missing=True)

    policy_version = snapshot.get("scoring_policy_version")
    if policy_version is None:
        return _unavailable(
            "LEGACY_SNAPSHOT",
            component_status,
            snapshot_as_of=snapshot.get("snapshot_as_of"),
            snapshot_run_id=snapshot.get("snapshot_run_id"),
        )
    policy = db.get_graph_scoring_policy(tenant_id, policy_version)
    if not policy:
        return _unavailable(
            "NO_SCORING_POLICY",
            component_status,
            snapshot_as_of=snapshot.get("snapshot_as_of"),
            snapshot_run_id=snapshot.get("snapshot_run_id"),
        )

    as_of = snapshot["snapshot_as_of"]
    as_of_date = as_of.date() if hasattr(as_of, "date") else as_of
    snapshot_age_days = (date.today() - as_of_date).days
    if snapshot_age_days > policy["snapshot_max_age_days"]:
        result = _unavailable(
            "STALE_SNAPSHOT",
            component_status,
            snapshot_as_of=as_of_date,
            snapshot_run_id=snapshot.get("snapshot_run_id"),
        )
        result["snapshot_age_days"] = snapshot_age_days
        result["scoring_policy_version"] = policy["policy_version"]
        result["scoring_strategy"] = policy["scoring_strategy"]
        return result

    inference_snapshot = _load_inference_snapshot(tenant_id, snapshot)
    candidate_started = time.perf_counter()
    ring_evaluation = evaluate_ring_candidate(
        inference_snapshot,
        CandidateNomination.from_dict(details),
        max_states=max(1, int(os.getenv("GRAPH_RING_MAX_STATES", "100000"))),
    )
    candidate_evaluation_ms = round(
        (time.perf_counter() - candidate_started) * 1000.0, 3
    )

    finding_map: dict[str, dict] = {}
    for user_id, role in roles:
        row = snapshot["users"].get(user_id)
        if not row:
            continue
        for raw in row.get("findings") or []:
            if not isinstance(raw, dict):
                continue
            finding_hash = raw.get("finding_hash")
            key = str(finding_hash or (
                f"{raw.get('pattern_type')}|{raw.get('finding_score')}|"
                f"{raw.get('detail')}"
            ))
            item = finding_map.setdefault(key, {
                "finding_hash": finding_hash,
                "pattern_type": raw.get("pattern_type") or "UnknownPattern",
                "finding_score": float(raw.get("finding_score") or 0),
                "derived_severity": str(
                    raw.get("severity") or "None"
                ).upper(),
                "nomination_ids": list(raw.get("nomination_ids") or []),
                "detail": raw.get("detail"),
                "total_amount": raw.get("total_amount", 0),
                "score_components": raw.get("score_components") or {},
                "enabled_for_routing": bool(
                    raw.get("enabled_for_routing", False)
                ),
                "applicable_roles": [
                    str(value).lower()
                    for value in (raw.get("applicable_roles") or [])
                ],
                "affected_roles": [],
                "affected_user_ids": [],
                "evaluation_mode": "SNAPSHOT_ROLE",
            })
            if role not in item["affected_roles"]:
                item["affected_roles"].append(role)
            if user_id not in item["affected_user_ids"]:
                item["affected_user_ids"].append(user_id)

    history_findings = list(finding_map.values())
    for finding in history_findings:
        roles_for_finding = set(finding["affected_roles"])
        if roles_for_finding == {"nominator", "beneficiary"}:
            finding["evidence_scope"] = "SHARED_HISTORY"
        elif "nominator" in roles_for_finding:
            finding["evidence_scope"] = "NOMINATOR_HISTORY"
        else:
            finding["evidence_scope"] = "BENEFICIARY_HISTORY"
        # Historical Ring membership is context, not evidence that this
        # nomination creates a ring. Other detectors retain their existing
        # role-based behavior until they receive candidate-aware evaluators.
        finding["routing_relevant"] = bool(
            finding["pattern_type"] != "Ring"
            and finding["enabled_for_routing"]
            and any(
                role in finding["applicable_roles"]
                for role in finding["affected_roles"]
            )
        )

    candidate_findings: list[dict] = []
    if ring_evaluation is not None:
        ring_policy = (inference_snapshot.scoring_policy.get("patterns") or {}).get(
            "Ring", {}
        )
        ring_data = ring_evaluation.to_dict()
        candidate_hash = hashlib.sha256(
            (
                f"{inference_snapshot.run_id}|Ring|{ring_evaluation.candidate_nomination_id}|"
                + ",".join(map(str, ring_evaluation.supporting_nomination_ids))
            ).encode("utf-8")
        ).hexdigest()
        path_text = " → ".join(map(str, ring_evaluation.path_user_ids))
        candidate_findings.append({
            "finding_hash": candidate_hash,
            "pattern_type": "Ring",
            "finding_score": ring_evaluation.score,
            "derived_severity": ring_evaluation.severity,
            "nomination_ids": list(ring_evaluation.supporting_nomination_ids),
            "detail": f"Current nomination completes directed ring: {path_text}",
            "total_amount": ring_evaluation.total_amount,
            "score_components": dict(ring_evaluation.score_components),
            "enabled_for_routing": bool(ring_policy.get("enabled_for_routing", True)),
            "applicable_roles": ["nominator", "beneficiary"],
            "affected_roles": ["nominator", "beneficiary"],
            "affected_user_ids": list(ring_evaluation.affected_user_ids),
            "evaluation_mode": ring_evaluation.evaluation_mode,
            "evidence_scope": ring_evaluation.evidence_scope,
            "routing_relevant": bool(ring_policy.get("enabled_for_routing", True)),
            "path_user_ids": list(ring_evaluation.path_user_ids),
            "supporting_nomination_ids": list(
                ring_evaluation.supporting_nomination_ids
            ),
            "candidate_nomination_id": ring_evaluation.candidate_nomination_id,
            "paths_considered": ring_evaluation.paths_considered,
            "states_visited": ring_evaluation.states_visited,
            "states_generated": ring_evaluation.states_generated,
            "detector_evaluation": ring_data,
        })

    findings = history_findings + candidate_findings

    candidates = [item for item in findings if item["routing_relevant"]]
    candidates.sort(
        key=lambda item: (
            -float(item["finding_score"]),
            str(item.get("finding_hash") or ""),
        )
    )
    winner = candidates[0] if candidates else None
    graph_score = round(float(winner["finding_score"]), 2) if winner else 0.0
    risk = _risk_level(graph_score, policy["thresholds"])
    affected = []
    for item in candidates:
        affected.extend(item["affected_user_ids"])

    # The score is derived from one winning finding. Keep the display-oriented
    # flags consistent with that decision; the full evidence remains available
    # in pattern_findings and detector_summary for audit and research.
    flags = [] if winner is None else [
        f"[Graph] {'/'.join(winner['affected_roles'])}: "
        f"{winner['pattern_type']} ({winner['finding_score']:.2f}, "
        f"{_risk_level(winner['finding_score'], policy['thresholds'])})"
    ]
    groups: dict[str, dict] = {}
    for item in findings:
        group = groups.setdefault(item['pattern_type'], {
            'pattern_type': item['pattern_type'], 'count': 0, 'scoring_count': 0,
            'highest_score': 0.0,
        })
        group['count'] += 1
        group['scoring_count'] += int(item['routing_relevant'])
        group['highest_score'] = max(group['highest_score'], item['finding_score'])
    summaries = sorted(groups.values(), key=lambda group: (-group['highest_score'], group['pattern_type']))
    winning_pattern_count = next(
        (group['scoring_count'] for group in summaries
         if winner and group['pattern_type'] == winner['pattern_type']),
        0,
    )
    result = {
        "model_available": True,
        "fraud_score": graph_score,
        "fraud_prob": None,
        "risk_level": risk,
        "source_severity": risk,
        "warning_flags": flags,
        "flagged": risk in ("MEDIUM", "HIGH", "CRITICAL"),
        "snapshot_as_of": as_of_date,
        "snapshot_run_id": snapshot.get("snapshot_run_id"),
        "snapshot_finding_count": snapshot.get("snapshot_finding_count", 0),
        "snapshot_age_days": snapshot_age_days,
        "affected_user_ids": list(dict.fromkeys(affected)),
        "pattern_findings": findings,
        "candidate_findings": candidate_findings,
        "nominator_history": [
            item for item in history_findings
            if item["evidence_scope"] == "NOMINATOR_HISTORY"
        ],
        "beneficiary_history": [
            item for item in history_findings
            if item["evidence_scope"] == "BENEFICIARY_HISTORY"
        ],
        "shared_history": [
            item for item in history_findings
            if item["evidence_scope"] == "SHARED_HISTORY"
        ],
        "winning_finding": winner,
        "detector_summary": summaries,
        "winning_finding_hash": winner.get("finding_hash") if winner else None,
        "winning_pattern_type": winner.get("pattern_type") if winner else None,
        "winning_pattern_count": winning_pattern_count,
        "scoring_strategy": policy["scoring_strategy"],
        "scoring_policy_version": policy["policy_version"],
        "score_thresholds": policy["thresholds"],
        "score_derivation": "maximum_relevant_finding",
        "candidate_evaluation_version": f"integrity-engine-core/{integrity_engine_version}",
        "candidate_evaluation_ms": candidate_evaluation_ms,
        "inference_snapshot_blob": snapshot.get("inference_snapshot_blob"),
        "inference_snapshot_sha256": snapshot.get("inference_snapshot_sha256"),
        "inference_snapshot_schema_version": snapshot.get(
            "inference_snapshot_schema_version"
        ),
        "inference_snapshot_generated_at": snapshot.get(
            "inference_snapshot_generated_at"
        ),
    }
    result.update(component_availability.available_metadata(component_status))
    return result
