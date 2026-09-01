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
        "winning_finding_hash": None,
        "winning_pattern_type": None,
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
    """Return the current Graph opinion; never raise to the handler."""
    try:
        return _assess_graph_inner(details, tenant_id, component_status)
    except Exception as exc:
        logger.error(
            "Graph assessment failed for nomination %s (tenant %d): %s",
            details.get("nomination_id"), tenant_id, exc, exc_info=True,
        )
        return _unavailable("INFERENCE_FAILED", component_status)


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

    flags = [
        f"[Graph] {'/'.join(item['affected_roles'])}: "
        f"{item['pattern_type']} ({item['finding_score']:.2f}, "
        f"{_risk_level(item['finding_score'], policy['thresholds'])})"
        for item in candidates
    ]
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
        "winning_finding_hash": winner.get("finding_hash") if winner else None,
        "winning_pattern_type": winner.get("pattern_type") if winner else None,
        "scoring_strategy": policy["scoring_strategy"],
        "scoring_policy_version": policy["policy_version"],
        "score_thresholds": policy["thresholds"],
        "score_derivation": "maximum_relevant_finding",
    }
    result.update(component_availability.available_metadata(component_status))
    return result
