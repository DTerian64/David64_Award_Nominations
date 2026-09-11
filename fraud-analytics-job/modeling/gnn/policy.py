"""Tenant-owned GNN training and serving policy.

The active row is intentionally read at the beginning of every tenant run.
There is no module-level cache: publishing a policy between scheduled job runs
must not require rebuilding or restarting the analytics container.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from .selection import GRAPH_ARCHITECTURES


@dataclass(frozen=True)
class GNNPolicy:
    policy_id: int
    policy_version: int
    training_enabled: bool
    inference_enabled: bool
    hidden_dim: int
    embed_dim: int
    epochs: int
    rolling_folds: int
    window_days: int
    embedding_retention_days: int
    stale_embedding_days: int
    minimum_training_samples: int
    minimum_users: int
    minimum_positives_per_split: int
    candidate_architectures: tuple[str, ...]
    selection_metric: str
    minimum_improvement_over_mlp: float
    incumbent_tie_tolerance: float
    minimum_eligible_graph_candidates: int
    low_threshold: float
    medium_threshold: float
    high_threshold: float
    critical_threshold: float
    explanation_enabled: bool
    explanation_minimum_risk: str

    def snapshot(self) -> dict:
        """JSON-safe immutable copy persisted with every trained bundle."""
        value = asdict(self)
        value["candidate_architectures"] = list(self.candidate_architectures)
        return value


_SELECT_ACTIVE = """
    SELECT TOP 1
        PolicyId, PolicyVersion, TrainingEnabled, InferenceEnabled,
        ConfigurationJson,
        ExplanationEnabled, ExplanationMinimumRisk
    FROM dbo.GNNScoringPolicies
    WHERE TenantId = ? AND Status = 'ACTIVE'
    ORDER BY PolicyVersion DESC
"""


def _validate(policy: GNNPolicy) -> None:
    invalid = sorted(set(policy.candidate_architectures) - set(GRAPH_ARCHITECTURES))
    if not policy.candidate_architectures:
        raise ValueError("GNN policy must configure at least one graph architecture")
    if invalid:
        raise ValueError(
            f"GNN policy contains unsupported architecture(s) {invalid}; "
            f"expected {GRAPH_ARCHITECTURES}"
        )
    if policy.selection_metric != "holdout_pr_auc":
        raise ValueError("GNN selection metric must be HOLDOUT_PR_AUC")
    positive_values = (
        policy.hidden_dim,
        policy.embed_dim,
        policy.epochs,
        policy.window_days,
        policy.embedding_retention_days,
        policy.stale_embedding_days,
        policy.minimum_training_samples,
        policy.minimum_users,
        policy.minimum_positives_per_split,
        policy.minimum_eligible_graph_candidates,
    )
    if any(value <= 0 for value in positive_values):
        raise ValueError("GNN configuration counts and dimensions must be positive")
    if policy.rolling_folds < 2:
        raise ValueError("GNN rolling fold count must be at least 2")
    if policy.embedding_retention_days < policy.stale_embedding_days:
        raise ValueError("GNN embedding retention must cover the staleness window")
    if policy.minimum_eligible_graph_candidates > len(
        policy.candidate_architectures
    ):
        raise ValueError(
            "GNN minimum eligible candidates exceeds configured architectures"
        )
    margins = (
        policy.minimum_improvement_over_mlp,
        policy.incumbent_tie_tolerance,
    )
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in margins):
        raise ValueError("GNN selection margins must be between 0 and 1")
    thresholds = (
        policy.low_threshold,
        policy.medium_threshold,
        policy.high_threshold,
        policy.critical_threshold,
    )
    if not all(math.isfinite(value) and 0 <= value <= 100 for value in thresholds):
        raise ValueError("GNN score thresholds must be between 0 and 100")
    if tuple(sorted(thresholds)) != thresholds:
        raise ValueError("GNN score thresholds must be ordered low through critical")
    if policy.explanation_minimum_risk not in {
        "NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"
    }:
        raise ValueError("GNN explanation minimum risk is invalid")


def load_active_policy(conn, tenant_id: int) -> GNNPolicy | None:
    """Read and validate the tenant's currently published GNN policy."""
    cur = conn.cursor()
    cur.execute(_SELECT_ACTIVE, tenant_id)
    row = cur.fetchone()
    if not row:
        return None
    try:
        configuration = json.loads(row[4])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("GNN ConfigurationJson is invalid") from exc
    if not isinstance(configuration, dict) or configuration.get("schema_version") != 1:
        raise ValueError("GNN ConfigurationJson must use schema_version 1")
    try:
        model = configuration["model"]
        training = configuration["training"]
        artifacts = configuration["artifacts"]
        selection = configuration["architecture_selection"]
        routing = configuration["score_routing"]
        raw_candidates = selection["candidate_architectures"]
    except (KeyError, TypeError) as exc:
        raise ValueError("GNN ConfigurationJson is missing required settings") from exc
    if not isinstance(raw_candidates, list) or not all(
        isinstance(item, str) for item in raw_candidates
    ):
        raise ValueError("GNN candidate architectures must be a JSON string array")

    try:
        policy = GNNPolicy(
            policy_id=int(row[0]),
            policy_version=int(row[1]),
            training_enabled=bool(row[2]),
            inference_enabled=bool(row[3]),
            hidden_dim=int(model["hidden_dimension"]),
            embed_dim=int(model["embedding_dimension"]),
            epochs=int(training["epochs"]),
            rolling_folds=int(training["rolling_fold_count"]),
            window_days=int(training["window_days"]),
            embedding_retention_days=int(artifacts["embedding_retention_days"]),
            stale_embedding_days=int(artifacts["stale_embedding_days"]),
            minimum_training_samples=int(training["minimum_training_samples"]),
            minimum_users=int(training["minimum_users"]),
            minimum_positives_per_split=int(
                training["minimum_positive_labels_per_split"]
            ),
            candidate_architectures=tuple(
                dict.fromkeys(
                    item.strip().lower() for item in raw_candidates if item.strip()
                )
            ),
            selection_metric=str(selection["selection_metric"]).lower(),
            minimum_improvement_over_mlp=float(
                selection["minimum_improvement_over_mlp"]
            ),
            incumbent_tie_tolerance=float(selection["incumbent_tie_tolerance"]),
            minimum_eligible_graph_candidates=int(
                selection["minimum_eligible_graph_candidates"]
            ),
            low_threshold=float(routing["low_threshold"]),
            medium_threshold=float(routing["medium_threshold"]),
            high_threshold=float(routing["high_threshold"]),
            critical_threshold=float(routing["critical_threshold"]),
            explanation_enabled=bool(row[5]),
            explanation_minimum_risk=str(row[6]).upper(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("GNN ConfigurationJson contains invalid settings") from exc
    _validate(policy)
    return policy
