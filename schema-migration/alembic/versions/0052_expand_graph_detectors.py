"""Add dense-block, temporal-burst, and super-beneficiary Graph detectors.

Existing active policies remain immutable for audit. Each tenant receives a
new active policy version containing the three detectors. Draft policies are
expanded in place because they have not been published.

Revision ID: 0052
Revises: 0051
Create Date: 2026-09-02
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


_ORDER = {
    "Ring": 1,
    "BipartiteDenseBlock": 2,
    "TemporalBurst": 3,
    "SuperNominator": 4,
    "SuperBeneficiary": 5,
    "CopyPaste": 6,
    "HiddenCandidate": 7,
    "Desert": 8,
}

_NEW_PATTERNS = {
    "BipartiteDenseBlock": {
        "enabled": True,
        "routing": True,
        "roles": ["nominator", "beneficiary"],
        "base": 30,
        "parameters": {
            "minimum_side_size": 2,
            "minimum_large_side_size": 3,
            "minimum_shared_neighbors": 2,
            "overlap_threshold": 0.6,
            "minimum_density": 0.65,
            "minimum_edges": 6,
            "repeat_reference": 2,
            "compactness_reference_days": 14,
            "amount_reference": 10000,
            "density_weight": 20,
            "overlap_weight": 15,
            "exclusivity_weight": 10,
            "repeat_weight": 10,
            "compactness_weight": 5,
            "exposure_weight": 10,
        },
    },
    "TemporalBurst": {
        "enabled": True,
        "routing": True,
        "roles": ["nominator", "beneficiary"],
        "base": 25,
        "parameters": {
            "burst_window_days": 3,
            "minimum_baseline_days": 21,
            "minimum_nominations": 8,
            "standard_deviations": 3,
            "overlap_suppression": 0.6,
            "count_reference": 20,
            "amount_reference": 10000,
            "excess_weight": 25,
            "volume_weight": 15,
            "participant_concentration_weight": 15,
            "temporal_compactness_weight": 10,
            "exposure_weight": 10,
        },
    },
    "SuperBeneficiary": {
        "enabled": True,
        "routing": False,
        "roles": ["beneficiary"],
        "base": 20,
        "parameters": {
            "minimum_count": 5,
            "minimum_unique_nominators": 4,
            "standard_deviations": 2,
            "median_multiplier": 3,
            "unique_reference": 10,
            "compactness_reference_days": 14,
            "amount_reference": 10000,
            "excess_weight": 20,
            "breadth_weight": 20,
            "repeat_concentration_weight": 10,
            "compactness_weight": 15,
            "exposure_weight": 15,
        },
    },
}


def _insert_pattern(conn, policy_id: int, pattern_type: str, config: dict) -> None:
    conn.execute(sa.text("""
        INSERT INTO dbo.GraphScoringPatternParameters (
            PolicyId, PatternType, DisplayOrder, Enabled, EnabledForRouting,
            ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
            ParametersJson, CreatedBy, UpdatedBy
        ) VALUES (
            :policy_id, :pattern_type, :display_order, :enabled, :routing,
            :roles, :base, 0, 100, :parameters,
            'migration:0052', 'migration:0052'
        )
    """), {
        "policy_id": policy_id,
        "pattern_type": pattern_type,
        "display_order": _ORDER[pattern_type],
        "enabled": int(config["enabled"]),
        "routing": int(config["routing"]),
        "roles": json.dumps(config["roles"], separators=(",", ":")),
        "base": config["base"],
        "parameters": json.dumps(config["parameters"], separators=(",", ":")),
    })


def _copy_existing_patterns(conn, old_policy_id: int, new_policy_id: int) -> None:
    rows = conn.execute(sa.text("""
        SELECT PatternType, Enabled, EnabledForRouting, ApplicableRolesJson,
               BaseScore, MinimumScore, MaximumScore, ParametersJson
        FROM dbo.GraphScoringPatternParameters
        WHERE PolicyId=:policy_id
    """), {"policy_id": old_policy_id}).fetchall()
    for row in rows:
        pattern_type = str(row[0])
        if pattern_type not in _ORDER or pattern_type in _NEW_PATTERNS:
            continue
        conn.execute(sa.text("""
            INSERT INTO dbo.GraphScoringPatternParameters (
                PolicyId, PatternType, DisplayOrder, Enabled, EnabledForRouting,
                ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
                ParametersJson, CreatedBy, UpdatedBy
            ) VALUES (
                :policy_id, :pattern_type, :display_order, :enabled, :routing,
                :roles, :base, :minimum, :maximum, :parameters,
                'migration:0052', 'migration:0052'
            )
        """), {
            "policy_id": new_policy_id,
            "pattern_type": pattern_type,
            "display_order": _ORDER[pattern_type],
            "enabled": int(row[1]),
            "routing": int(row[2]),
            "roles": row[3],
            "base": row[4],
            "minimum": row[5],
            "maximum": row[6],
            "parameters": row[7],
        })


def _expand_draft(conn, policy_id: int) -> None:
    conn.execute(sa.text("""
        UPDATE dbo.GraphScoringPatternParameters
        SET DisplayOrder=DisplayOrder + 100
        WHERE PolicyId=:policy_id
    """), {"policy_id": policy_id})
    for pattern_type, display_order in _ORDER.items():
        if pattern_type in _NEW_PATTERNS:
            continue
        conn.execute(sa.text("""
            UPDATE dbo.GraphScoringPatternParameters
            SET DisplayOrder=:display_order,
                UpdatedAt=SYSUTCDATETIME(),
                UpdatedBy='migration:0052'
            WHERE PolicyId=:policy_id AND PatternType=:pattern_type
        """), {
            "policy_id": policy_id,
            "pattern_type": pattern_type,
            "display_order": display_order,
        })
    for pattern_type, config in _NEW_PATTERNS.items():
        _insert_pattern(conn, policy_id, pattern_type, config)


def upgrade() -> None:
    conn = op.get_bind()
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
            SET Status='RETIRED', UpdatedAt=SYSUTCDATETIME(),
                UpdatedBy='migration:0052'
            WHERE PolicyId=:policy_id
        """), {"policy_id": old_policy_id})
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
                'migration:0052', 'migration:0052',
                SYSUTCDATETIME(), 'migration:0052'
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
        }).scalar_one())
        _copy_existing_patterns(conn, old_policy_id, new_policy_id)
        for pattern_type, config in _NEW_PATTERNS.items():
            _insert_pattern(conn, new_policy_id, pattern_type, config)

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
                UpdatedBy='migration:0052'
            WHERE PolicyId=:policy_id
        """), {"policy_id": policy_id, "version": next_version})
        _expand_draft(conn, policy_id)

    op.execute("DELETE FROM dbo.UserGraphFlags;")
    op.execute("""
        UPDATE dbo.IntegrityComponentStatus
        SET ServingStatus='UNAVAILABLE',
            ReasonCode='GRAPH_REFRESH_REQUIRED',
            ReasonDetail='Graph detector catalogue expanded; run Graph Analytics.',
            UpdatedAt=SYSUTCDATETIME(),
            UpdatedBy='migration:0052'
        WHERE Component='GRAPH';
    """)


def downgrade() -> None:
    conn = op.get_bind()
    created = conn.execute(sa.text("""
        SELECT PolicyId, TenantId
        FROM dbo.GraphScoringPolicies
        WHERE CreatedBy='migration:0052'
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
        """), {"tenant_id": tenant_id, "policy_id": policy_id}).scalar_one_or_none()
        if predecessor is not None:
            conn.execute(sa.text("""
                UPDATE dbo.GraphScoringChangeRequests
                SET PolicyId=CASE WHEN PolicyId=:policy_id THEN :predecessor ELSE PolicyId END,
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
                    UpdatedBy='migration:0052-downgrade'
                WHERE PolicyId=:predecessor
            """), {"predecessor": int(predecessor)})

    draft_ids = conn.execute(sa.text("""
        SELECT PolicyId FROM dbo.GraphScoringPolicies WHERE Status='DRAFT'
    """)).scalars().all()
    for policy_id in draft_ids:
        conn.execute(sa.text("""
            DELETE FROM dbo.GraphScoringPatternParameters
            WHERE PolicyId=:policy_id
              AND PatternType IN (
                  'BipartiteDenseBlock', 'TemporalBurst', 'SuperBeneficiary'
              )
        """), {"policy_id": int(policy_id)})
        conn.execute(sa.text("""
            UPDATE dbo.GraphScoringPatternParameters
            SET DisplayOrder=CASE PatternType
                WHEN 'Ring' THEN 1
                WHEN 'SuperNominator' THEN 2
                WHEN 'CopyPaste' THEN 3
                WHEN 'HiddenCandidate' THEN 4
                WHEN 'Desert' THEN 5
                ELSE DisplayOrder
            END
            WHERE PolicyId=:policy_id
        """), {"policy_id": int(policy_id)})

    op.execute("DELETE FROM dbo.UserGraphFlags;")
    op.execute("""
        UPDATE dbo.IntegrityComponentStatus
        SET ServingStatus='UNAVAILABLE',
            ReasonCode='GRAPH_REFRESH_REQUIRED',
            ReasonDetail='Graph detector catalogue rolled back; run Graph Analytics.',
            UpdatedAt=SYSUTCDATETIME(),
            UpdatedBy='migration:0052-downgrade'
        WHERE Component='GRAPH';
    """)
