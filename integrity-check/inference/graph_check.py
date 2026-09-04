"""Independent nomination-time Graph Analytics opinion."""

from __future__ import annotations

import logging
from datetime import date

from . import component_availability
from utils import db

logger = logging.getLogger("integrity_check.graph_check")


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
        reason = "INVALID_SNAPSHOT" if isinstance(exc, db.InvalidGraphSnapshot) else "INFERENCE_FAILED"
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
            "snapshot_as_of": result.get("snapshot_as_of"),
            "snapshot_run_id": result.get("snapshot_run_id"),
            "snapshot_age_days": result.get("snapshot_age_days"),
            "scoring_strategy": result.get("scoring_strategy"),
            "scoring_policy_version": result.get("scoring_policy_version"),
            "winning_finding_hash": result.get("winning_finding_hash"),
            "winning_pattern_type": result.get("winning_pattern_type"),
            "winning_pattern_count": result.get("winning_pattern_count", 0),
            "finding_count": len(result.get("pattern_findings") or []),
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
            })
            if role not in item["affected_roles"]:
                item["affected_roles"].append(role)
            if user_id not in item["affected_user_ids"]:
                item["affected_user_ids"].append(user_id)

    findings = list(finding_map.values())
    for finding in findings:
        finding["routing_relevant"] = bool(
            finding["enabled_for_routing"]
            and any(
                role in finding["applicable_roles"]
                for role in finding["affected_roles"]
            )
        )

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
        "winning_finding": winner,
        "detector_summary": summaries,
        "winning_finding_hash": winner.get("finding_hash") if winner else None,
        "winning_pattern_type": winner.get("pattern_type") if winner else None,
        "winning_pattern_count": winning_pattern_count,
        "scoring_strategy": policy["scoring_strategy"],
        "scoring_policy_version": policy["policy_version"],
        "score_thresholds": policy["thresholds"],
        "score_derivation": "maximum_relevant_finding",
    }
    result.update(component_availability.available_metadata(component_status))
    return result
