"""Configure Ring at 60 days and other windowed Graph patterns at 180 days.

Revision ID: 0069
Revises: 0068
"""
import json
import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

WINDOWS = {"Ring": 60, "BipartiteDenseBlock": 180, "TemporalBurst": 180,
           "SuperNominator": 180, "SuperBeneficiary": 180,
           "CopyPasteFraud": 180, "HiddenCandidate": 180, "LowRecognitionNominator": 180}


def _with_detector_windows(raw):
    configuration = json.loads(raw) if raw else {}
    graph = configuration.setdefault("graph_pattern", {})
    graph["detection_window_days"] = 180
    graph.setdefault("detector_windows", {}).update(WINDOWS)
    graph["detector_windows"].pop("CopyPaste", None)
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
                updated_at=SYSUTCDATETIME(), updated_by='migration:0069'
            WHERE TenantId=:tenant_id
        """), {"tenant_id": tenant_id, "configuration": _with_detector_windows(raw)})


def downgrade():
    raise RuntimeError("Use a forward configuration change; do not discard tenant detector windows.")
