"""Centralize separate Graph and GNN windows in tenant integrity_config.

Graph becomes 180 days. Each GNN window is copied from its active policy
without changing its value; an existing tenant GNN window is preserved.
No scoring thresholds, users, nominations, or published artifacts change.
Published Graph snapshots and trained GNN models retain their recorded
windows until their respective stages successfully publish replacements.

Revision ID: 0067
Revises: 0066
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None
WINDOW_DAYS = 180


def _with_graph_window(raw: str | None, gnn_window_days: int | None = None) -> str:
    configuration = json.loads(raw) if raw else {}
    graph = configuration.setdefault("graph_pattern", {})
    graph["detection_window_days"] = WINDOW_DAYS
    if gnn_window_days is not None:
        configuration.setdefault("gnn", {}).setdefault("window_days", gnn_window_days)
    return json.dumps(configuration, separators=(",", ":"), ensure_ascii=False)


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("""
        SELECT t.TenantId, CAST(t.integrity_config AS nvarchar(max)),
               g.window_days
        FROM dbo.Tenants t WITH (UPDLOCK, HOLDLOCK)
        OUTER APPLY (
            SELECT TOP 1 TRY_CONVERT(int, JSON_VALUE(
                CAST(p.ConfigurationJson AS nvarchar(max)), '$.training.window_days'
            )) AS window_days
            FROM dbo.GNNScoringPolicies p
            WHERE p.TenantId=t.TenantId AND p.Status='ACTIVE'
            ORDER BY p.PolicyVersion DESC
        ) g
        ORDER BY t.TenantId
    """)).fetchall()
    for tenant_id, raw, gnn_window_days in rows:
        conn.execute(sa.text("""
            UPDATE dbo.Tenants SET integrity_config=:configuration
            WHERE TenantId=:tenant_id
        """), {
            "tenant_id": tenant_id,
            "configuration": _with_graph_window(
                raw, int(gnn_window_days) if gnn_window_days is not None else None,
            ),
        })
    # Existing drafts must not inadvertently restore the previous window
    # when subsequently published. Keep active/retired policy history intact.
    conn.execute(sa.text("""
        UPDATE dbo.GraphScoringPolicies
        SET DetectionWindowDays=:window_days,
            UpdatedAt=SYSUTCDATETIME(), UpdatedBy='migration:0067'
        WHERE Status='DRAFT'
    """), {"window_days": WINDOW_DAYS})


def downgrade() -> None:
    raise RuntimeError(
        "Migration 0067 preserves tenant configuration but cannot reconstruct "
        "previous per-tenant windows. Use an explicit forward configuration change."
    )
