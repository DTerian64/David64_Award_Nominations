"""Limit Graph Ring detection to tight three- or four-user cycles.

Active Graph policies remain immutable for audit. Each tenant receives a new
active version whose Ring candidate-evaluation policy has max_ring_size=4.
Draft policies are updated in place because they have not been published.

Revision ID: 0063
Revises: 0062
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def _set_ring_limit(conn, policy_id: int, actor: str) -> None:
    conn.execute(sa.text("""
        UPDATE dbo.GraphScoringPatternParameters
        SET ParametersJson=JSON_MODIFY(
                ParametersJson, '$.compactness_decay_span', 5
            ),
            CandidateEvaluationJson=JSON_MODIFY(
                CandidateEvaluationJson, '$.max_ring_size', 4
            ),
            UpdatedAt=SYSUTCDATETIME(),
            UpdatedBy=:actor
        WHERE PolicyId=:policy_id AND PatternType='Ring'
    """), {"policy_id": policy_id, "actor": actor})


def upgrade() -> None:
    conn = op.get_bind()
    actor = "migration:0063"
    active_rows = conn.execute(sa.text("""
        SELECT PolicyId, TenantId, ScoringStrategy,
               LowThreshold, MediumThreshold, HighThreshold, CriticalThreshold,
               DetectionWindowDays, SnapshotMaxAgeDays
        FROM dbo.GraphScoringPolicies
        WHERE Status='ACTIVE'
        ORDER BY TenantId
    """)).fetchall()

    for row in active_rows:
        old_policy_id = int(row[0])
        tenant_id = int(row[1])
        next_version = int(conn.execute(sa.text("""
            SELECT ISNULL(MAX(PolicyVersion), 0) + 1
            FROM dbo.GraphScoringPolicies
            WHERE TenantId=:tenant_id
        """), {"tenant_id": tenant_id}).scalar_one())

        conn.execute(sa.text("""
            UPDATE dbo.GraphScoringPolicies
            SET Status='RETIRED', UpdatedAt=SYSUTCDATETIME(), UpdatedBy=:actor
            WHERE PolicyId=:policy_id
        """), {"policy_id": old_policy_id, "actor": actor})

        new_policy_id = int(conn.execute(sa.text("""
            INSERT INTO dbo.GraphScoringPolicies (
                TenantId, PolicyVersion, Status, ScoringStrategy,
                LowThreshold, MediumThreshold, HighThreshold, CriticalThreshold,
                DetectionWindowDays, SnapshotMaxAgeDays,
                CreatedBy, UpdatedBy, PublishedAt, PublishedBy
            )
            OUTPUT INSERTED.PolicyId
            VALUES (
                :tenant_id, :version, 'ACTIVE', :strategy,
                :low, :medium, :high, :critical, :window_days, :max_age,
                :actor, :actor, SYSUTCDATETIME(), :actor
            )
        """), {
            "tenant_id": tenant_id,
            "version": next_version,
            "strategy": row[2],
            "low": row[3],
            "medium": row[4],
            "high": row[5],
            "critical": row[6],
            "window_days": row[7],
            "max_age": row[8],
            "actor": actor,
        }).scalar_one())

        conn.execute(sa.text("""
            INSERT INTO dbo.GraphScoringPatternParameters (
                PolicyId, PatternType, DisplayOrder, Enabled, EnabledForRouting,
                ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
                ParametersJson, CandidateEvaluationJson, CreatedBy, UpdatedBy
            )
            SELECT :new_policy_id, PatternType, DisplayOrder, Enabled,
                   EnabledForRouting, ApplicableRolesJson, BaseScore,
                   MinimumScore, MaximumScore, ParametersJson,
                   CandidateEvaluationJson, :actor, :actor
            FROM dbo.GraphScoringPatternParameters
            WHERE PolicyId=:old_policy_id
        """), {
            "new_policy_id": new_policy_id,
            "old_policy_id": old_policy_id,
            "actor": actor,
        })
        _set_ring_limit(conn, new_policy_id, actor)

    draft_rows = conn.execute(sa.text("""
        SELECT PolicyId, TenantId
        FROM dbo.GraphScoringPolicies
        WHERE Status='DRAFT'
    """)).fetchall()
    for draft_row in draft_rows:
        policy_id, tenant_id = int(draft_row[0]), int(draft_row[1])
        next_version = int(conn.execute(sa.text("""
            SELECT ISNULL(MAX(PolicyVersion), 0) + 1
            FROM dbo.GraphScoringPolicies
            WHERE TenantId=:tenant_id
        """), {"tenant_id": tenant_id}).scalar_one())
        conn.execute(sa.text("""
            UPDATE dbo.GraphScoringPolicies
            SET PolicyVersion=:version, UpdatedAt=SYSUTCDATETIME(),
                UpdatedBy=:actor
            WHERE PolicyId=:policy_id
        """), {
            "version": next_version,
            "actor": actor,
            "policy_id": policy_id,
        })
        _set_ring_limit(conn, policy_id, actor)

    op.execute("DELETE FROM dbo.UserGraphFlags;")
    op.execute("""
        UPDATE dbo.IntegrityComponentStatus
        SET ServingStatus='UNAVAILABLE',
            ReasonCode='GRAPH_REFRESH_REQUIRED',
            ReasonDetail='Graph Ring size limit changed; run Graph Analytics.',
            UpdatedAt=SYSUTCDATETIME(),
            UpdatedBy='migration:0063'
        WHERE Component='GRAPH';
    """)


def downgrade() -> None:
    conn = op.get_bind()
    created = conn.execute(sa.text("""
        SELECT PolicyId, TenantId
        FROM dbo.GraphScoringPolicies
        WHERE CreatedBy='migration:0063'
        ORDER BY TenantId
    """)).fetchall()

    for row in created:
        policy_id, tenant_id = int(row[0]), int(row[1])
        predecessor = conn.execute(sa.text("""
            SELECT TOP 1 PolicyId
            FROM dbo.GraphScoringPolicies
            WHERE TenantId=:tenant_id
              AND PolicyId<>:policy_id
              AND Status='RETIRED'
            ORDER BY PolicyVersion DESC
        """), {
            "tenant_id": tenant_id,
            "policy_id": policy_id,
        }).scalar_one_or_none()
        if predecessor is not None:
            conn.execute(sa.text("""
                UPDATE dbo.GraphScoringChangeRequests
                SET PolicyId=CASE
                        WHEN PolicyId=:policy_id THEN :predecessor ELSE PolicyId
                    END,
                    ResolvedPolicyId=CASE
                        WHEN ResolvedPolicyId=:policy_id THEN :predecessor
                        ELSE ResolvedPolicyId
                    END
                WHERE PolicyId=:policy_id OR ResolvedPolicyId=:policy_id
            """), {
                "policy_id": policy_id,
                "predecessor": int(predecessor),
            })
        conn.execute(sa.text("""
            DELETE FROM dbo.GraphScoringPolicies WHERE PolicyId=:policy_id
        """), {"policy_id": policy_id})
        if predecessor is not None:
            conn.execute(sa.text("""
                UPDATE dbo.GraphScoringPolicies
                SET Status='ACTIVE', UpdatedAt=SYSUTCDATETIME(),
                    UpdatedBy='migration:0063-downgrade'
                WHERE PolicyId=:predecessor
            """), {"predecessor": int(predecessor)})

    conn.execute(sa.text("""
        UPDATE dbo.GraphScoringPatternParameters
        SET ParametersJson=JSON_MODIFY(
                ParametersJson, '$.compactness_decay_span', NULL
            ),
            CandidateEvaluationJson=JSON_MODIFY(
                CandidateEvaluationJson, '$.max_ring_size', 8
            ),
            UpdatedAt=SYSUTCDATETIME(),
            UpdatedBy='migration:0063-downgrade'
        WHERE PatternType='Ring' AND UpdatedBy='migration:0063'
    """))

    op.execute("DELETE FROM dbo.UserGraphFlags;")
    op.execute("""
        UPDATE dbo.IntegrityComponentStatus
        SET ServingStatus='UNAVAILABLE',
            ReasonCode='GRAPH_REFRESH_REQUIRED',
            ReasonDetail='Graph Ring size limit rolled back; run Graph Analytics.',
            UpdatedAt=SYSUTCDATETIME(),
            UpdatedBy='migration:0063-downgrade'
        WHERE Component='GRAPH';
    """)
