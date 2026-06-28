"""Add integrity_config to dbo.Tenants

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-28

Context
-------
Adds a per-tenant integrity_config JSON column to dbo.Tenants.

This column drives all tenant-specific configuration for the fraud analytics
pipeline and the integrity-check service.  The initial schema covers two
namespaces:

  graph_pattern
      detection_window_days  — rolling window (in days) used by
                               graph_pattern_detector.py when loading
                               Approved/Paid nominations.  Determines how far
                               back each pattern detector looks.
                               Default (env var): 180 days.
                               Recommended production value: 365 days.

  score_routing
      critical_threshold     — fraud_score ≥ this → auto-reject (default 80)
      high_threshold         — fraud_score ≥ this → HRBP review  (default 60)
      medium_threshold       — fraud_score ≥ this → HRBP review  (default 40)
      low_threshold          — fraud_score ≥ this → LOW risk     (default 20)

Example value:
{
  "graph_pattern": { "detection_window_days": 365 },
  "score_routing":  { "critical_threshold": 80, "high_threshold": 60,
                      "medium_threshold": 40,  "low_threshold":  20 }
}

NULL means "use system defaults" — no row required for tenants that don't
need custom thresholds.  Additional namespaces can be added to the JSON
without a schema migration.

Downgrade
---------
Drops the column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :t AND COLUMN_NAME = :c AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name, "c": column_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "Tenants", "integrity_config"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants ADD integrity_config NVARCHAR(MAX) NULL"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "Tenants", "integrity_config"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants DROP COLUMN integrity_config"
        ))
