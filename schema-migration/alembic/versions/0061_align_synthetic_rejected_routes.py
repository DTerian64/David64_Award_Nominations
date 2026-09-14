"""Align synthetic rejected nominations with their HRBP fraud route.

Revision ID: 0061
Revises: 0060

The initial synthetic historical import assigned MANAGER_APPROVAL to every
decision envelope.  That contradicts the Rejected nomination state and also
removes confirmed fraudulent topology from later GNN message-passing history.
This data-only repair gives rejected synthetic FRAUD rows the HRBP fraud route.
"""

from alembic import op


revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE decision_result
        SET FinalRoute = 'HRBP_REVIEW',
            ReviewScope = 'FRAUD',
            UpdatedAt = SYSUTCDATETIME()
        FROM dbo.IntegrityDecisionResults AS decision_result
        INNER JOIN dbo.Nominations AS nomination
            ON nomination.NominationId = decision_result.NominationId
        INNER JOIN dbo.Tenants AS tenant
            ON tenant.TenantId = decision_result.TenantId
        WHERE tenant.is_synthetic = 1
          AND nomination.Status = 'Rejected'
          AND decision_result.TrainingDisposition = 'FRAUD'
          AND decision_result.TrainingDispositionSource =
              'SYNTHETIC_GROUND_TRUTH'
          AND decision_result.RoutingRule = 'SYNTHETIC_HISTORICAL_IMPORT'
          AND (
              decision_result.FinalRoute <> 'HRBP_REVIEW'
              OR ISNULL(decision_result.ReviewScope, '') <> 'FRAUD'
          );
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE decision_result
        SET FinalRoute = 'MANAGER_APPROVAL',
            ReviewScope = NULL,
            UpdatedAt = SYSUTCDATETIME()
        FROM dbo.IntegrityDecisionResults AS decision_result
        INNER JOIN dbo.Nominations AS nomination
            ON nomination.NominationId = decision_result.NominationId
        INNER JOIN dbo.Tenants AS tenant
            ON tenant.TenantId = decision_result.TenantId
        WHERE tenant.is_synthetic = 1
          AND nomination.Status = 'Rejected'
          AND decision_result.TrainingDisposition = 'FRAUD'
          AND decision_result.TrainingDispositionSource =
              'SYNTHETIC_GROUND_TRUTH'
          AND decision_result.RoutingRule = 'SYNTHETIC_HISTORICAL_IMPORT'
          AND decision_result.FinalRoute = 'HRBP_REVIEW'
          AND decision_result.ReviewScope = 'FRAUD';
    """)
