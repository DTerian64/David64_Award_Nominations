"""Add per-tenant graph analytics score-routing defaults.

Revision ID: 0044
Revises: 0043
Create Date: 2026-08-24

Graph analytics routing is independently configurable at
``integrity_config.graph.score_routing``. Existing graph keys and threshold
values are preserved; only missing values receive defaults matching the current
severity-to-score behavior.
"""

import json

import sqlalchemy as sa
from alembic import op


revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


_GRAPH_ROUTING_DEFAULTS = {
    "low_threshold": 25,
    "medium_threshold": 50,
    "high_threshold": 75,
    "critical_threshold": 100,
}


def _load_config(tenant_id: int, raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        config = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"Tenant {tenant_id} has invalid integrity_config JSON; "
            "refusing to overwrite it while adding graph score thresholds."
        ) from exc
    if not isinstance(config, dict):
        raise ValueError(f"Tenant {tenant_id} integrity_config must be a JSON object.")
    return config


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT TenantId, integrity_config FROM dbo.Tenants")
    ).fetchall()

    for tenant_id, raw in rows:
        config = _load_config(int(tenant_id), raw)
        graph = config.get("graph")
        if not isinstance(graph, dict):
            graph = {}
        routing = graph.get("score_routing")
        if not isinstance(routing, dict):
            routing = {}

        changed = False
        for key, default in _GRAPH_ROUTING_DEFAULTS.items():
            if key not in routing:
                routing[key] = default
                changed = True

        if not changed and config.get("graph") is graph:
            continue

        graph["score_routing"] = routing
        config["graph"] = graph
        conn.execute(
            sa.text(
                "UPDATE dbo.Tenants SET integrity_config = :config "
                "WHERE TenantId = :tenant_id"
            ),
            {
                "config": json.dumps(config, separators=(",", ":")),
                "tenant_id": tenant_id,
            },
        )


def downgrade() -> None:
    # Tenant configuration is operational data. Do not erase graph thresholds
    # that administrators may have calibrated after upgrade.
    pass
