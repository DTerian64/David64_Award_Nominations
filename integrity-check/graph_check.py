"""Independent live graph-analytics opinion for a nomination.

The weekly detector has already assigned a categorical severity to each graph
finding and materialised the latest per-user snapshot.  Live scoring consumes
that severity directly.  It does not reuse RF output and does not invent a
weighted pseudo-model over the RF graph feature columns.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from utils import db

logger = logging.getLogger("integrity_check.graph_check")

_RISK_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
_GRAPH_SCORE = {"NONE": 0, "LOW": 25, "MEDIUM": 50, "HIGH": 75, "CRITICAL": 100}
_STALE_SNAPSHOT_DAYS = 14


def _unavailable(reason: str) -> dict:
    return {
        "model_available": False,
        "unavailable_reason": reason,
        "fraud_score": 0,
        "fraud_prob": None,
        "risk_level": "NONE",
        "warning_flags": [],
        "flagged": False,
        "snapshot_as_of": None,
        "affected_user_ids": [],
    }


def assess_graph(details: dict, tenant_id: int) -> dict:
    """Return the current graph component score; never raise to the handler."""
    try:
        return _assess_graph_inner(details, tenant_id)
    except Exception as exc:
        logger.error(
            "Graph assessment failed for nomination %s (tenant %d): %s",
            details.get("nomination_id"), tenant_id, exc, exc_info=True,
        )
        return _unavailable("exception")


def _assess_graph_inner(details: dict, tenant_id: int) -> dict:
    roles = {
        details["nominator_id"]: "nominator",
        details["beneficiary_id"]: "beneficiary",
    }
    if details.get("approver_id") is not None:
        roles[details["approver_id"]] = "approver"

    snapshot = db.get_graph_component_snapshot(tenant_id, list(roles))
    if snapshot is None:
        return _unavailable("no_snapshot")

    users = snapshot["users"]
    risk = "NONE"
    affected: list[int] = []
    flags: list[str] = []

    for user_id, role in roles.items():
        row = users.get(user_id)
        if not row:
            continue
        severity = str(row.get("highest_severity") or "NONE").upper()
        if severity not in _RISK_RANK:
            logger.warning("Ignoring unknown graph severity %r for user %d", severity, user_id)
            severity = "NONE"
        if _RISK_RANK[severity] > _RISK_RANK[risk]:
            risk = severity
        if severity != "NONE":
            affected.append(user_id)

        prefix = f"[Graph] {role}"
        if row.get("is_in_ring"):
            flags.append(f"{prefix} participates in a ring pattern")
        if row.get("is_super_nominator"):
            flags.append(f"{prefix} is a super-nominator outlier")
        if row.get("is_in_copy_paste_cluster"):
            size = row.get("copy_paste_cluster_size", 0)
            flags.append(f"{prefix} is in a copy/paste cluster (size {size})")
        if row.get("has_transactional_language"):
            flags.append(f"{prefix} has transactional-language findings")
        if row.get("is_approver_affinity"):
            flags.append(f"{prefix} has an approver-affinity finding")

    if risk != "NONE" and not flags:
        flags.append(f"[Graph] {risk.lower()} graph pattern")

    as_of = snapshot["snapshot_as_of"]
    if as_of < date.today() - timedelta(days=_STALE_SNAPSHOT_DAYS):
        flags.append(f"[Graph] snapshot is {(date.today() - as_of).days} days old")

    return {
        "model_available": True,
        "fraud_score": _GRAPH_SCORE[risk],
        "fraud_prob": None,
        "risk_level": risk,
        "warning_flags": flags,
        "flagged": risk in ("MEDIUM", "HIGH", "CRITICAL"),
        "snapshot_as_of": as_of,
        "affected_user_ids": affected,
    }
