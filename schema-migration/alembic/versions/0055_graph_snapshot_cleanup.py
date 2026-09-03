"""Complete Graph run history, lean user snapshots, and scoring Super Beneficiary.

Revision ID: 0055
Revises: 0054

Cutover: pause Graph jobs and integrity-check, migrate, deploy all three services,
then refresh Graph before resuming inference. Historical findings are retained
and explicitly marked as partial. No detector formulas or thresholds change.
"""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

LEGACY_COLUMNS = (
    "IsInRing", "RingMaxUserCount", "RingMaxNominationCount", "IsSuperNominator",
    "IsInCopyPasteCluster", "CopyPasteClusterSize", "IsApproverAffinity", "HighestSeverity",
)


def _check_dependencies():
    conn = op.get_bind()
    for column in LEGACY_COLUMNS:
        dependencies = conn.execute(sa.text("""
            SELECT OBJECT_SCHEMA_NAME(d.referencing_id) + '.' + OBJECT_NAME(d.referencing_id)
            FROM sys.sql_expression_dependencies d
            WHERE d.referenced_id=OBJECT_ID('dbo.UserGraphFlags')
              AND d.referenced_minor_id IN (0, COLUMNPROPERTY(d.referenced_id, :column, 'ColumnId'))
              AND NOT EXISTS (SELECT 1 FROM sys.default_constraints c WHERE c.object_id=d.referencing_id)
            UNION ALL
            SELECT i.name FROM sys.indexes i
            JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
            JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
            WHERE i.object_id=OBJECT_ID('dbo.UserGraphFlags') AND c.name=:column
            UNION ALL
            SELECT OBJECT_NAME(f.constraint_object_id) FROM sys.foreign_key_columns f
            WHERE (f.parent_object_id=OBJECT_ID('dbo.UserGraphFlags')
                   AND f.parent_column_id=COLUMNPROPERTY(f.parent_object_id, :column, 'ColumnId'))
               OR (f.referenced_object_id=OBJECT_ID('dbo.UserGraphFlags')
                   AND f.referenced_column_id=COLUMNPROPERTY(f.referenced_object_id, :column, 'ColumnId'))
        """), {"column": column}).fetchall()
        if dependencies:
            raise RuntimeError(f"0055: resolve database dependencies on UserGraphFlags.{column}: {dependencies}")


def _publish_policies():
    conn = op.get_bind()
    policies = conn.execute(sa.text("""
        SELECT PolicyId, TenantId FROM dbo.GraphScoringPolicies WHERE Status='ACTIVE'
    """)).fetchall()
    for policy_id, tenant_id in policies:
        conn.execute(sa.text("""
            UPDATE dbo.GraphScoringPolicies SET Status='RETIRED',
                UpdatedAt=SYSUTCDATETIME(), UpdatedBy='migration:0055' WHERE PolicyId=:id
        """), {"id": policy_id})
        new_id = conn.execute(sa.text("""
            INSERT INTO dbo.GraphScoringPolicies (
                TenantId, PolicyVersion, Status, ScoringStrategy, LowThreshold,
                MediumThreshold, HighThreshold, CriticalThreshold, DetectionWindowDays,
                SnapshotMaxAgeDays, CreatedBy, UpdatedBy, PublishedAt, PublishedBy)
            OUTPUT INSERTED.PolicyId
            SELECT TenantId, (SELECT MAX(PolicyVersion)+1 FROM dbo.GraphScoringPolicies WHERE TenantId=:tenant),
                'ACTIVE', ScoringStrategy, LowThreshold, MediumThreshold, HighThreshold,
                CriticalThreshold, DetectionWindowDays, SnapshotMaxAgeDays,
                'migration:0055', 'migration:0055', SYSUTCDATETIME(), 'migration:0055'
            FROM dbo.GraphScoringPolicies WHERE PolicyId=:id
        """), {"id": policy_id, "tenant": tenant_id}).scalar_one()
        conn.execute(sa.text("""
            INSERT INTO dbo.GraphScoringPatternParameters (
                PolicyId, PatternType, DisplayOrder, Enabled, EnabledForRouting,
                ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
                ParametersJson, CreatedBy, UpdatedBy)
            SELECT :new_id, PatternType, DisplayOrder,
                CASE WHEN PatternType='SuperBeneficiary' THEN 1 ELSE Enabled END,
                CASE WHEN PatternType='SuperBeneficiary' THEN 1 ELSE EnabledForRouting END,
                ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
                ParametersJson, 'migration:0055', 'migration:0055'
            FROM dbo.GraphScoringPatternParameters WHERE PolicyId=:id
        """), {"new_id": new_id, "id": policy_id})
    # Drafts are editable; ensure a later draft publication doesn't undo the decision.
    conn.execute(sa.text("""
        UPDATE x SET Enabled=1, EnabledForRouting=1, UpdatedBy='migration:0055', UpdatedAt=SYSUTCDATETIME()
        FROM dbo.GraphScoringPatternParameters x JOIN dbo.GraphScoringPolicies p ON p.PolicyId=x.PolicyId
        WHERE p.Status='DRAFT' AND x.PatternType='SuperBeneficiary'
    """))


def upgrade():
    _check_dependencies()
    _publish_policies()
    op.add_column("GraphPatternFindings", sa.Column("SnapshotComplete", sa.Boolean(), nullable=False, server_default=sa.text("0")), schema="dbo")
    op.drop_index("ux_graphpatternfindings_hash", table_name="GraphPatternFindings", schema="dbo")
    op.create_index("ux_graphpatternfindings_run_hash", "GraphPatternFindings",
                    ["TenantId", "RunId", "FindingHash"], unique=True, schema="dbo",
                    mssql_where=sa.text("FindingHash IS NOT NULL"))
    for column in LEGACY_COLUMNS:
        # Only names from the fixed list above enter this DDL. Drop SQL Server's
        # generated default names before removing the unused columns.
        op.execute(sa.text(f"""
            DECLARE @constraint_name sysname, @ddl nvarchar(max);
            SELECT @constraint_name=d.name FROM sys.default_constraints d
            JOIN sys.columns c ON c.object_id=d.parent_object_id AND c.column_id=d.parent_column_id
            WHERE d.parent_object_id=OBJECT_ID('dbo.UserGraphFlags') AND c.name='{column}';
            IF @constraint_name IS NOT NULL BEGIN
                SET @ddl=N'ALTER TABLE dbo.UserGraphFlags DROP CONSTRAINT '+QUOTENAME(@constraint_name);
                EXEC sp_executesql @ddl;
            END;
            ALTER TABLE dbo.UserGraphFlags DROP COLUMN [{column}];
        """))
    op.execute("""
        UPDATE dbo.IntegrityComponentStatus SET ServingStatus='UNAVAILABLE',
            ReasonCode='GRAPH_REFRESH_REQUIRED',
            ReasonDetail='Graph snapshot contract updated; run Graph Analytics.',
            UpdatedAt=SYSUTCDATETIME(), UpdatedBy='migration:0055'
        WHERE Component='GRAPH';
    """)


def downgrade():
    raise RuntimeError("0055 is intentionally irreversible: complete run history cannot restore cross-run uniqueness or discarded summaries.")
