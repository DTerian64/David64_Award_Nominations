"""Add per-tenant GNN score-routing defaults to integrity_config.

Revision ID: 0041
Revises: 0040
Create Date: 2026-08-22

Random Forest routing remains at ``integrity_config.score_routing``. GNN
routing is independently configurable at ``integrity_config.gnn.score_routing``.
Existing GNN keys and existing threshold values are preserved; only missing
thresholds receive defaults.
"""

import json

import sqlalchemy as sa
from alembic import op


revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


_GNN_ROUTING_DEFAULTS = {
    "low_threshold": 25,
    "medium_threshold": 45,
    "high_threshold": 65,
    "critical_threshold": 85,
}


def _load_config(tenant_id: int, raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        config = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"Tenant {tenant_id} has invalid integrity_config JSON; "
            "refusing to overwrite it while adding GNN thresholds."
        ) from exc
    if not isinstance(config, dict):
        raise ValueError(
            f"Tenant {tenant_id} integrity_config must be a JSON object."
        )
    return config


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT TenantId, integrity_config FROM dbo.Tenants")
    ).fetchall()

    for tenant_id, raw in rows:
        config = _load_config(int(tenant_id), raw)
        gnn = config.get("gnn")
        if not isinstance(gnn, dict):
            gnn = {}
        routing = gnn.get("score_routing")
        if not isinstance(routing, dict):
            routing = {}

        changed = False
        for key, default in _GNN_ROUTING_DEFAULTS.items():
            if key not in routing:
                routing[key] = default
                changed = True

        if not changed and config.get("gnn") is gnn:
            continue

        gnn["score_routing"] = routing
        config["gnn"] = gnn
        conn.execute(
            sa.text(
                "UPDATE dbo.Tenants "
                "SET integrity_config = :config "
                "WHERE TenantId = :tenant_id"
            ),
            {
                "config": json.dumps(config, separators=(",", ":")),
                "tenant_id": tenant_id,
            },
        )


def downgrade() -> None:
    # Tenant configuration is operational data. Do not delete GNN thresholds on
    # downgrade because administrators may have calibrated them after upgrade.
    pass
