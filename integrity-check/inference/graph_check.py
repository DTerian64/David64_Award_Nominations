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

import numpy as np

from integrity_engine import (
    CandidateNomination,
    EvaluationLimitExceeded,
    GraphInferenceSnapshot,
    __version__ as integrity_engine_version,
    evaluate_candidate_detectors,
    evaluate_candidate_edge_for_ring,
    evaluate_no_finding,
)

from . import component_availability
from utils import db

logger = logging.getLogger("integrity_check.graph_check")

_snapshot_cache: OrderedDict[
    tuple[int, str, str], tuple[GraphInferenceSnapshot, float]
] = OrderedDict()
_snapshot_cache_lock = threading.Lock()
_copy_paste_matrix_cache: OrderedDict[
    tuple[int, str], tuple[tuple[int, ...], np.ndarray, float]
] = OrderedDict()


def _get_copy_paste_embed_model():
    """Share the service's sentence-transformer instance, not RF evidence."""
    from .random_forest_check import _get_embed_model
    return _get_embed_model("all-MiniLM-L6-v2")


def _evict_idle_snapshots(now: float | None = None) -> int:
    """Drop large Graph artifacts after MODEL_IDLE_TTL_SECONDS of inactivity."""
    now = time.monotonic() if now is None else now
    idle_ttl = max(1, int(os.getenv("MODEL_IDLE_TTL_SECONDS", "1800")))
    with _snapshot_cache_lock:
        expired = [
            key
            for key, (_snapshot, last_used) in _snapshot_cache.items()
            if now - last_used > idle_ttl
        ]
        for key in expired:
            del _snapshot_cache[key]
        active_runs = {(key[0], key[1]) for key in _snapshot_cache}
        expired_matrices = [
            key for key, (_ids, _matrix, last_used) in _copy_paste_matrix_cache.items()
            if now - last_used > idle_ttl or key not in active_runs
        ]
        for key in expired_matrices:
            del _copy_paste_matrix_cache[key]
    if expired:
        logger.info("Evicted %d idle Graph snapshot(s)", len(expired))
    return len(expired)


def _load_inference_snapshot(tenant_id: int, metadata: dict) -> GraphInferenceSnapshot:
    """Load and verify the immutable candidate-evaluation graph artifact."""
    blob_name = metadata.get("inference_snapshot_blob")
    digest = metadata.get("inference_snapshot_sha256")
    run_id = metadata.get("snapshot_run_id")
    if not all(isinstance(value, str) and value for value in (blob_name, digest, run_id)):
        raise db.InvalidGraphSnapshot("Graph candidate-evaluation artifact is missing")
    key = (tenant_id, run_id, digest)
    now = time.monotonic()
    _evict_idle_snapshots(now)
    with _snapshot_cache_lock:
        entry = _snapshot_cache.get(key)
        if entry is not None:
            cached, _last_used = entry
            _snapshot_cache[key] = (cached, now)
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
        # The producer intentionally publishes a .json.gz blob with
        # Content-Encoding: gzip and hashes the stored compressed bytes. Azure
        # otherwise expands that encoding during transport, which would make
        # the downloaded bytes differ from the checksum and size recorded in
        # the serving metadata.
        compressed = client.get_blob_client(
            container=container, blob=blob_name
        ).download_blob(decompress=False).readall()
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
        _snapshot_cache[key] = (snapshot, time.monotonic())
        _snapshot_cache.move_to_end(key)
        maximum = max(1, int(os.getenv("GRAPH_SNAPSHOT_CACHE_SIZE", "8")))
        while len(_snapshot_cache) > maximum:
            _snapshot_cache.popitem(last=False)
    return snapshot


def _copy_paste_evidence(
    snapshot: GraphInferenceSnapshot,
    candidate: CandidateNomination,
) -> tuple[set[int], list[float]]:
    """Return the exact similarity component formed by the candidate text."""
    config = (snapshot.scoring_policy.get("patterns") or {}).get("CopyPaste") or {}
    if not config.get("enabled", False):
        return set(), []
    parameters = config.get("parameters") or {}
    threshold = float(parameters.get("similarity_threshold", 0.92))
    history = [
        item for item in snapshot.nominations
        if item.nomination_id != candidate.nomination_id
        and item.created_at < candidate.created_at
        and item.status in ("Pending", "Approved", "Paid")
        and item.description
        and len(item.description.strip()) > 20
    ]
    if not history or len(candidate.description.strip()) <= 20:
        return set(), []

    cache_key = (snapshot.tenant_id, snapshot.run_id)
    now = time.monotonic()
    with _snapshot_cache_lock:
        cached = _copy_paste_matrix_cache.get(cache_key)
        if cached is not None:
            nomination_ids, matrix, _last_used = cached
            _copy_paste_matrix_cache[cache_key] = (nomination_ids, matrix, now)
            _copy_paste_matrix_cache.move_to_end(cache_key)
        else:
            nomination_ids, matrix = (), np.empty((0, 0), dtype=np.float32)

    expected_ids = tuple(item.nomination_id for item in history)
    if nomination_ids != expected_ids:
        embedding_bytes = db.get_nomination_embedding_bytes(list(expected_ids))
        vectors: list[np.ndarray | None] = []
        missing: list[int] = []
        for index, item in enumerate(history):
            raw = embedding_bytes.get(item.nomination_id)
            if raw:
                vectors.append(np.frombuffer(raw, dtype=np.float32).copy())
            else:
                vectors.append(None)
                missing.append(index)
        if missing:
            model = _get_copy_paste_embed_model()
            encoded = np.asarray(model.encode(
                [history[index].description for index in missing],
                normalize_embeddings=True,
                show_progress_bar=False,
            ), dtype=np.float32)
            for encoded_index, history_index in enumerate(missing):
                vectors[history_index] = encoded[encoded_index]
        matrix = np.stack([vector for vector in vectors if vector is not None])
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = matrix / np.maximum(norms, 1e-12)
        nomination_ids = expected_ids
        with _snapshot_cache_lock:
            _copy_paste_matrix_cache[cache_key] = (nomination_ids, matrix, now)
            _copy_paste_matrix_cache.move_to_end(cache_key)
            maximum = max(1, int(os.getenv("GRAPH_SNAPSHOT_CACHE_SIZE", "8")))
            while len(_copy_paste_matrix_cache) > maximum:
                _copy_paste_matrix_cache.popitem(last=False)

    candidate_vector = np.asarray(
        _get_copy_paste_embed_model().encode(
            [candidate.description],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0],
        dtype=np.float32,
    )
    candidate_vector /= max(float(np.linalg.norm(candidate_vector)), 1e-12)

    component_indexes = set(np.flatnonzero(matrix @ candidate_vector >= threshold).tolist())
    frontier = list(component_indexes)
    while frontier:
        index = frontier.pop()
        neighbors = set(np.flatnonzero(matrix @ matrix[index] >= threshold).tolist())
        new_indexes = neighbors - component_indexes
        component_indexes.update(new_indexes)
        frontier.extend(new_indexes)

    component_vectors = [candidate_vector] + [
        matrix[index] for index in sorted(component_indexes)
    ]
    qualifying: list[float] = []
    if len(component_vectors) > 1:
        component_matrix = np.stack(component_vectors)
        similarities = component_matrix @ component_matrix.T
        for left in range(len(component_vectors)):
            for right in range(left + 1, len(component_vectors)):
                value = float(similarities[left, right])
                if value >= threshold:
                    qualifying.append(value)
    return {
        nomination_ids[index] for index in component_indexes
    }, qualifying


def _derive_graph_nomination_severity(
    graph_nomination_score: float,
    thresholds: dict[str, float],
) -> str:
    if graph_nomination_score >= thresholds["critical"]:
        return "CRITICAL"
    if graph_nomination_score >= thresholds["high"]:
        return "HIGH"
    if graph_nomination_score >= thresholds["medium"]:
        return "MEDIUM"
    if graph_nomination_score >= thresholds["low"]:
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
        "candidate_detector_scores": [],
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
            "candidate_detector_scores": result.get("candidate_detector_scores") or [],
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
            "candidate_evaluation": result.get("candidate_evaluation"),
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
    ring_policy = (inference_snapshot.scoring_policy.get("patterns") or {}).get(
        "Ring", {}
    )
    ring_enabled = bool(ring_policy.get("enabled", False))
    candidate_policy = ring_policy.get("candidate_evaluation") or {}
    candidate_started = time.perf_counter()
    candidate = CandidateNomination.from_dict(details)
    ring_evaluation = (
        evaluate_candidate_edge_for_ring(inference_snapshot, candidate)
        if ring_enabled else None
    )
    copy_paste_ids, copy_paste_similarities = _copy_paste_evidence(
        inference_snapshot,
        candidate,
    )
    other_detector_evaluations = evaluate_candidate_detectors(
        inference_snapshot,
        candidate,
        copy_paste_component_nomination_ids=copy_paste_ids,
        copy_paste_qualifying_similarities=copy_paste_similarities,
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
        # Historical membership is participant context, not evidence that the
        # current nomination creates a detector finding. The six routing
        # detectors are evaluated against the candidate below.
        candidate_aware_types = {
            "Ring", "BipartiteDenseBlock", "TemporalBurst",
            "SuperNominator", "SuperBeneficiary", "CopyPaste",
        }
        finding["routing_relevant"] = bool(
            finding["pattern_type"] not in candidate_aware_types
            and finding["enabled_for_routing"]
            and any(
                role in finding["applicable_roles"]
                for role in finding["affected_roles"]
            )
        )

    candidate_findings: list[dict] = []
    candidate_detector_scores: list[dict] = []
    if ring_evaluation is not None:
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
            "affected_role_user_ids": {
                "nominator": int(details["nominator_id"]),
                "beneficiary": int(details["beneficiary_id"]),
            },
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
            "search_status": ring_evaluation.search_status,
            "search_complete": ring_evaluation.search_complete,
            "score_semantics": ring_evaluation.score_semantics,
            "configured_max_states": ring_evaluation.configured_max_states,
            "configured_max_ring_size": (
                ring_evaluation.configured_max_ring_size
            ),
            "limit_strategy": ring_evaluation.limit_strategy,
            "pruned_unreachable": ring_evaluation.pruned_unreachable,
            "pruned_by_bound": ring_evaluation.pruned_by_bound,
            "pruned_by_dominance": ring_evaluation.pruned_by_dominance,
            "remaining_score_upper_bound": (
                ring_evaluation.remaining_score_upper_bound
            ),
            "detector_evaluation": ring_data,
        })
        candidate_detector_scores.append({
            "detector": "Ring",
            "score": ring_evaluation.score,
            "severity": ring_evaluation.severity,
            "eligible": True,
            "enabled_for_routing": bool(
                ring_policy.get("enabled_for_routing", True)
            ),
            "state": (
                "SCORING" if ring_policy.get("enabled_for_routing", True)
                else "ANALYTICS_ONLY"
            ),
            "eligibility_reasons": [],
            "score_components": dict(ring_evaluation.score_components),
        })
    elif ring_enabled:
        candidate_detector_scores.append(evaluate_no_finding(
            inference_snapshot,
            "Ring",
            "Candidate edge does not complete a directed ring.",
        ).to_dict())

    for evaluation in other_detector_evaluations:
        evaluation_data = evaluation.to_dict()
        candidate_detector_scores.append({
            "detector": evaluation.detector,
            "score": evaluation.score,
            "severity": evaluation.severity,
            "eligible": evaluation.eligible,
            "enabled_for_routing": evaluation.enabled_for_routing,
            "state": evaluation.state,
            "eligibility_reasons": list(evaluation.eligibility_reasons),
            "score_components": dict(evaluation.score_components),
            "detail": evaluation.detail,
        })
        if not evaluation.eligible:
            continue
        config = (inference_snapshot.scoring_policy.get("patterns") or {}).get(
            evaluation.detector, {}
        )
        applicable_roles = [
            str(role).lower()
            for role in (config.get("applicable_roles") or [])
        ]
        candidate_hash = hashlib.sha256(
            (
                f"{inference_snapshot.run_id}|{evaluation.detector}|"
                f"{candidate.nomination_id}"
            ).encode("utf-8")
        ).hexdigest()
        candidate_findings.append({
            "finding_hash": candidate_hash,
            "pattern_type": evaluation.detector,
            "finding_score": evaluation.score,
            "derived_severity": evaluation.severity,
            "nomination_ids": list(evaluation.supporting_nomination_ids),
            "detail": evaluation.detail,
            "score_components": dict(evaluation.score_components),
            "enabled_for_routing": evaluation.enabled_for_routing,
            "applicable_roles": applicable_roles,
            "affected_roles": applicable_roles,
            "affected_role_user_ids": {
                "nominator": int(details["nominator_id"]),
                "beneficiary": int(details["beneficiary_id"]),
            },
            "affected_user_ids": list(evaluation.affected_user_ids),
            "evaluation_mode": "CANDIDATE_EDGE",
            "evidence_scope": "CURRENT_NOMINATION",
            "routing_relevant": evaluation.state == "SCORING",
            "candidate_nomination_id": candidate.nomination_id,
            "detector_evaluation": evaluation_data,
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
    risk = _derive_graph_nomination_severity(
        graph_score, policy["thresholds"]
    )
    affected = []
    for item in candidates:
        affected.extend(item["affected_user_ids"])

    # The score is derived from one winning finding. Keep the display-oriented
    # flags consistent with that decision; the full evidence remains available
    # in pattern_findings and detector_summary for audit and research.
    flags = [] if winner is None else [
        f"[Graph] {'/'.join(winner['affected_roles'])}: "
        f"{winner['pattern_type']} ({winner['finding_score']:.2f}, "
        f"{_derive_graph_nomination_severity(winner['finding_score'], policy['thresholds'])})"
    ]
    pattern_configs = inference_snapshot.scoring_policy.get("patterns") or {}
    evaluations_by_type = {
        item["detector"]: item for item in candidate_detector_scores
    }
    groups: dict[str, dict] = {
        pattern_type: {
            "pattern_type": pattern_type,
            "count": 0,
            "scoring_count": 0,
            "highest_score": 0.0,
            "highest_scoring_score": 0.0,
            "enabled": bool(config.get("enabled", True)),
            "enabled_for_routing": bool(
                config.get("enabled_for_routing", False)
            ),
            "candidate_score": evaluations_by_type.get(pattern_type, {}).get("score"),
            "candidate_state": evaluations_by_type.get(pattern_type, {}).get("state"),
            "eligibility_reasons": evaluations_by_type.get(pattern_type, {}).get(
                "eligibility_reasons", []
            ),
        }
        for pattern_type, config in pattern_configs.items()
    }
    for item in findings:
        group = groups.setdefault(item['pattern_type'], {
            'pattern_type': item['pattern_type'], 'count': 0, 'scoring_count': 0,
            'highest_score': 0.0, 'highest_scoring_score': 0.0,
            'enabled': True,
            'enabled_for_routing': bool(item.get('enabled_for_routing', False)),
        })
        group['count'] += 1
        group['scoring_count'] += int(item['routing_relevant'])
        group['highest_score'] = max(group['highest_score'], item['finding_score'])
        if item['routing_relevant']:
            group['highest_scoring_score'] = max(
                group['highest_scoring_score'], item['finding_score']
            )
    summaries = sorted(
        groups.values(),
        key=lambda group: (-group['highest_scoring_score'], group['pattern_type']),
    )
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
        "candidate_detector_scores": candidate_detector_scores,
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
        "candidate_evaluation": (
            ring_evaluation.to_dict() if ring_evaluation is not None else {
                "detector": "Ring",
                "search_status": "COMPLETE",
                "search_complete": True,
                "score_semantics": "EXACT",
                "finding_present": False,
                "configured_max_states": int(
                    candidate_policy.get("max_states", 100_000)
                ),
                "configured_max_ring_size": int(
                    candidate_policy.get("max_ring_size", 8)
                ),
                "limit_strategy": str(
                    candidate_policy.get("limit_strategy", "BEST_EVIDENCE")
                ),
            }
        ),
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
