"""Create dbo.Nomination_Logs — persistent per-nomination log trail (SOC 2)

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-13

Context
-------
The Nomination Logs drawer currently reads ContainerAppConsoleLogs from Log
Analytics, which ages out (~7-30 day retention) — leaving no history for a
nomination after that window. This table persists the same nomination-scoped
log lines at runtime so the trail survives indefinitely.

Producers (backend, integrity-check, auxiliary-service, payroll-broker) attach
a non-blocking logging handler that batch-inserts every INFO+ record carrying a
nomination_id. Rows are append-only.

    log_time  = event emission time (what the drawer sorts by)
    created_at = row persistence time (audit) — within ms of log_time
    created_by / updated_by = the emitting service marker (svc:<name>)
"""

import sqlalchemy as sa
from alembic import op

revision      = "0035"
down_revision = "0034"
branch_labels = None
depends_on    = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    return conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t"
        ),
        {"t": name},
    ).fetchone() is not None


def upgrade() -> None:
    if _table_exists("Nomination_Logs"):
        return

    op.execute("""
        CREATE TABLE dbo.Nomination_Logs (
            log_id        BIGINT         IDENTITY(1,1) NOT NULL,
            nomination_id INT            NOT NULL,
            tenant_id     INT            NULL,
            log_time      DATETIME2(3)   NOT NULL,
            level         NVARCHAR(20)   NOT NULL,
            service       NVARCHAR(100)  NOT NULL,
            logger        NVARCHAR(200)  NULL,
            message       NVARCHAR(MAX)  NOT NULL,
            message_id    NVARCHAR(100)  NULL,
            details       NVARCHAR(MAX)  NULL,
            exception     NVARCHAR(MAX)  NULL,
            created_at    DATETIME2      NOT NULL
                          CONSTRAINT DF_Nomination_Logs_created_at DEFAULT SYSUTCDATETIME(),
            created_by    NVARCHAR(256)  NULL,
            updated_at    DATETIME2      NOT NULL
                          CONSTRAINT DF_Nomination_Logs_updated_at DEFAULT SYSUTCDATETIME(),
            updated_by    NVARCHAR(256)  NULL,
            CONSTRAINT PK_Nomination_Logs PRIMARY KEY CLUSTERED (log_id)
        );
    """)

    # Drawer query: filter by nomination_id, order by log_time.
    op.execute("""
        CREATE INDEX IX_Nomination_Logs_nom_time
            ON dbo.Nomination_Logs (nomination_id, log_time);
    """)


def downgrade() -> None:
    if _table_exists("Nomination_Logs"):
        op.execute("DROP TABLE dbo.Nomination_Logs;")
