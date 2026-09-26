"""Add the shared RF/Tabular MLP window without changing Graph/GNN windows.

Revision ID: 0068
Revises: 0067
"""
import json
import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def _with_tabular_window(raw):
    configuration = json.loads(raw) if raw else {}
    configuration.setdefault("tabular", {}).setdefault("window_days", 365)
    return json.dumps(configuration, separators=(",", ":"), ensure_ascii=False)


def upgrade():
    conn = op.get_bind()
    rows = conn.execute(sa.text("""
        SELECT TenantId, CAST(integrity_config AS nvarchar(max))
        FROM dbo.Tenants WITH (UPDLOCK, HOLDLOCK) ORDER BY TenantId
    """)).fetchall()
    for tenant_id, raw in rows:
        conn.execute(sa.text("""
            UPDATE dbo.Tenants SET integrity_config=:configuration,
                updated_at=SYSUTCDATETIME(), updated_by='migration:0068'
            WHERE TenantId=:tenant_id
        """), {"tenant_id": tenant_id, "configuration": _with_tabular_window(raw)})


def downgrade():
    raise RuntimeError("Use a forward configuration change; existing tenant windows are preserved.")
