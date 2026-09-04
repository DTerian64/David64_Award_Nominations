"""Restore cross-run evidence identity and reset the approved synthetic tenants.

Revision ID: 0056
Revises: 0055

Pause Graph jobs and integrity-check before applying; deploy the matching code
and refresh all three tenants before resuming processing. Does not drop tables,
reseed identities, or change nomination decisions/logs.
"""
import os
import sqlalchemy as sa
from alembic import op

revision = '0056'
down_revision = '0055'
branch_labels = None
depends_on = None


def _preflight():
    conn = op.get_bind()
    database = conn.execute(sa.text('SELECT DB_NAME()')).scalar_one()
    dependencies = conn.execute(sa.text("""
        SELECT name FROM sys.foreign_keys
        WHERE referenced_object_id IN (OBJECT_ID('dbo.GraphPatternFindings'), OBJECT_ID('dbo.UserGraphFlags'))
        UNION ALL
        SELECT name FROM sys.triggers WHERE is_disabled=0
          AND parent_id IN (OBJECT_ID('dbo.GraphPatternFindings'), OBJECT_ID('dbo.UserGraphFlags'))
    """)).fetchall()
    if dependencies:
        raise RuntimeError(f'0056: review references/triggers before resetting Graph data: {dependencies}')
    outside_scope = conn.execute(sa.text("""
        SELECT COUNT(*) FROM (
            SELECT TenantId FROM dbo.GraphPatternFindings WHERE TenantId NOT IN (1,2,3)
            UNION ALL
            SELECT TenantId FROM dbo.UserGraphFlags WHERE TenantId NOT IN (1,2,3)
        ) x
    """)).scalar_one()
    if outside_scope:
        raise RuntimeError('0056: Graph data exists outside approved tenants 1, 2, 3; reset refused')
    existing = conn.execute(sa.text("""
        SELECT (SELECT COUNT_BIG(*) FROM dbo.GraphPatternFindings)
             + (SELECT COUNT_BIG(*) FROM dbo.UserGraphFlags)
    """)).scalar_one()
    if existing and os.getenv('GRAPH_FINDINGS_RESET_DATABASE') != database:
        raise RuntimeError('0056: set GRAPH_FINDINGS_RESET_DATABASE to the exact target database name after pausing workers and backing up synthetic Graph data')


def upgrade():
    _preflight()
    # Transactional, tenant-scoped data reset. Keep FindingId identity sequence:
    # old links must not silently start identifying newly generated findings.
    op.execute('DELETE FROM dbo.GraphPatternFindings WHERE TenantId IN (1,2,3)')
    op.execute('DELETE FROM dbo.UserGraphFlags WHERE TenantId IN (1,2,3)')
    op.drop_index('ux_graphpatternfindings_run_hash', table_name='GraphPatternFindings', schema='dbo')
    op.create_index('ux_graphpatternfindings_hash', 'GraphPatternFindings',
                    ['TenantId', 'FindingHash'], unique=True, schema='dbo',
                    mssql_where=sa.text('FindingHash IS NOT NULL'))
    op.execute("""
        UPDATE dbo.IntegrityComponentStatus SET ServingStatus='UNAVAILABLE',
            ServingVersion=NULL, ServingAsOf=NULL, RunId=NULL, DiagnosticsJson=NULL,
            ReasonCode='GRAPH_REFRESH_REQUIRED',
            ReasonDetail='Graph evidence reset; run Graph Analytics with policy-independent hashes.',
            UpdatedAt=SYSUTCDATETIME(), UpdatedBy='migration:0056'
        WHERE Component='GRAPH' AND TenantId IN (1,2,3)
    """)


def downgrade():
    raise RuntimeError('0056 is irreversible: synthetic Graph history was intentionally cleared; use an approved backup for recovery')
