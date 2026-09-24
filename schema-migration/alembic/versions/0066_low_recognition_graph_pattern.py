"""Add the analytics-only low-recognition Graph finding to every policy.

Published policies remain immutable. Active policies are versioned; draft
policies receive the new detector in place.

Revision ID: 0066
Revises: 0065
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

ACTOR = "migration:0066"
PATTERN = "LowRecognitionNominator"
PARAMETERS = {
    "minimum_nominations_made": 8,
    "minimum_distinct_beneficiaries": 4,
    "maximum_nominations_received": 1,
    "nominations_reference": 20,
    "beneficiaries_reference": 10,
    "activity_weight": 10,
    "breadth_weight": 10,
}


def _add_pattern(conn, policy_id: int) -> None:
    conn.execute(sa.text("""
        INSERT INTO dbo.GraphScoringPatternParameters (
            PolicyId, PatternType, DisplayOrder, Enabled, EnabledForRouting,
            ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
            ParametersJson, CandidateEvaluationJson, CreatedBy, UpdatedBy
        ) VALUES (
            :policy_id, :pattern, 9, 1, 0, :roles, 10, 0, 100,
            :parameters, :candidate_evaluation, :actor, :actor
        )
    """), {
        "policy_id": policy_id,
        "pattern": PATTERN,
        "roles": '["nominator"]',
        "parameters": json.dumps(PARAMETERS, separators=(",", ":")),
        "candidate_evaluation": "{}",
        "actor": ACTOR,
    })


def upgrade() -> None:
    conn = op.get_bind()
    active_rows = conn.execute(sa.text("""
        SELECT PolicyId, TenantId, ScoringStrategy, LowThreshold,
               MediumThreshold, HighThreshold, CriticalThreshold,
               DetectionWindowDays, SnapshotMaxAgeDays
        FROM dbo.GraphScoringPolicies WITH (UPDLOCK, HOLDLOCK)
        WHERE Status='ACTIVE' ORDER BY TenantId
    """)).fetchall()
    for row in active_rows:
        old_id, tenant_id = int(row[0]), int(row[1])
        if conn.execute(sa.text("""
            SELECT 1 FROM dbo.GraphScoringPatternParameters
            WHERE PolicyId=:policy_id AND PatternType=:pattern
        """), {"policy_id": old_id, "pattern": PATTERN}).first():
            continue
        version = int(conn.execute(sa.text("""
            SELECT ISNULL(MAX(PolicyVersion), 0) + 1
            FROM dbo.GraphScoringPolicies WHERE TenantId=:tenant_id
        """), {"tenant_id": tenant_id}).scalar_one())
        conn.execute(sa.text("""
            UPDATE dbo.GraphScoringPolicies
            SET Status='RETIRED', UpdatedAt=SYSUTCDATETIME(), UpdatedBy=:actor
            WHERE PolicyId=:policy_id AND Status='ACTIVE'
        """), {"policy_id": old_id, "actor": ACTOR})
        new_id = int(conn.execute(sa.text("""
            INSERT INTO dbo.GraphScoringPolicies (
                TenantId, PolicyVersion, Status, ScoringStrategy,
                LowThreshold, MediumThreshold, HighThreshold, CriticalThreshold,
                DetectionWindowDays, SnapshotMaxAgeDays,
                CreatedBy, UpdatedBy, PublishedAt, PublishedBy
            ) OUTPUT INSERTED.PolicyId VALUES (
                :tenant_id, :version, 'ACTIVE', :strategy,
                :low, :medium, :high, :critical, :window_days, :max_age,
                :actor, :actor, SYSUTCDATETIME(), :actor
            )
        """), {
            "tenant_id": tenant_id, "version": version, "strategy": row[2],
            "low": row[3], "medium": row[4], "high": row[5],
            "critical": row[6], "window_days": row[7], "max_age": row[8],
            "actor": ACTOR,
        }).scalar_one())
        conn.execute(sa.text("""
            INSERT INTO dbo.GraphScoringPatternParameters (
                PolicyId, PatternType, DisplayOrder, Enabled, EnabledForRouting,
                ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
                ParametersJson, CandidateEvaluationJson, CreatedBy, UpdatedBy
            )
            SELECT :new_id, PatternType, DisplayOrder, Enabled,
                   EnabledForRouting, ApplicableRolesJson, BaseScore,
                   MinimumScore, MaximumScore, ParametersJson,
                   CandidateEvaluationJson, :actor, :actor
            FROM dbo.GraphScoringPatternParameters WHERE PolicyId=:old_id
        """), {"new_id": new_id, "old_id": old_id, "actor": ACTOR})
        _add_pattern(conn, new_id)

    drafts = conn.execute(sa.text("""
        SELECT PolicyId FROM dbo.GraphScoringPolicies WHERE Status='DRAFT'
    """)).fetchall()
    for row in drafts:
        policy_id = int(row[0])
        if not conn.execute(sa.text("""
            SELECT 1 FROM dbo.GraphScoringPatternParameters
            WHERE PolicyId=:policy_id AND PatternType=:pattern
        """), {"policy_id": policy_id, "pattern": PATTERN}).first():
            _add_pattern(conn, policy_id)


def downgrade() -> None:
    raise RuntimeError(
        "Graph policy 0066 is published audit history; rollback requires an "
        "explicit replacement policy migration."
    )
