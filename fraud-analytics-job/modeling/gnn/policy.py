"""Tenant-owned GNN training and serving policy.

The active row is intentionally read at the beginning of every tenant run.
There is no module-level cache: publishing a policy between scheduled job runs
must not require rebuilding or restarting the analytics container.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from .evaluators.selection_by_holdout_pr_auc import GRAPH_ARCHITECTURES
from .specialists.contracts import (
    BEHAVIOR_TRACKS,
    SERVING_MODES,
    SERVING_MODE_V2,
    SpecialistTrackPolicy,
)
from .specialists.feature_contracts import FEATURE_CONTRACTS


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
    serving_mode: str
    specialist_tracks: tuple[SpecialistTrackPolicy, ...]
    aggregation_method: str
    mixed_minimum_specialists: int

    def snapshot(self) -> dict:
        """JSON-safe immutable copy persisted with every trained bundle."""
        value = asdict(self)
        value["candidate_architectures"] = list(self.candidate_architectures)
        value["specialist_tracks"] = [
            track.snapshot() for track in self.specialist_tracks
        ]
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
    if policy.serving_mode not in SERVING_MODES:
        raise ValueError(f"Unsupported GNN serving mode: {policy.serving_mode}")
    if policy.aggregation_method != "maximum_calibrated_probability":
        raise ValueError("GNN specialist aggregation method is unsupported")
    if policy.mixed_minimum_specialists < 2:
        raise ValueError("GNN mixed evidence requires at least two specialists")
    track_keys = [track.key for track in policy.specialist_tracks]
    if len(track_keys) != len(set(track_keys)) or set(track_keys) - set(BEHAVIOR_TRACKS):
        raise ValueError("GNN specialist tracks must use unique supported keys")
    for track in policy.specialist_tracks:
        invalid = sorted(
            set(track.candidate_architectures) - set(GRAPH_ARCHITECTURES)
        )
        if invalid or not track.candidate_architectures:
            raise ValueError(
                f"GNN specialist {track.key} has invalid candidate architectures"
            )
        counts = (
            track.minimum_train_positives,
            track.minimum_train_negatives,
            track.minimum_holdout_positives,
            track.minimum_holdout_negatives,
            track.minimum_evaluable_temporal_folds,
        )
        if any(value <= 0 for value in counts):
            raise ValueError(f"GNN specialist {track.key} label gates must be positive")
        bounded = (
            track.maximum_fold_pr_auc_range,
            track.minimum_improvement_over_mlp,
            track.maximum_holdout_brier_score,
            track.incumbent_refresh_tolerance,
            track.architecture_switch_tolerance,
        )
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in bounded):
            raise ValueError(f"GNN specialist {track.key} metric gates are invalid")
        if track.maximum_holdout_inference_ms <= 0:
            raise ValueError(f"GNN specialist {track.key} latency gate must be positive")
        if track.feature_contract not in FEATURE_CONTRACTS:
            raise ValueError(
                f"GNN specialist {track.key} feature contract is unsupported"
            )


def _specialist_tracks(configuration: dict) -> tuple[SpecialistTrackPolicy, ...]:
    raw_tracks = configuration.get("behavior_tracks") or {}
    if not isinstance(raw_tracks, dict):
        raise ValueError("GNN behavior_tracks must be an object")
    tracks = []
    for key, raw in raw_tracks.items():
        normalized = str(key).strip().upper()
        if not isinstance(raw, dict):
            raise ValueError(f"GNN specialist {normalized} policy must be an object")
        candidates = raw.get("candidate_architectures") or []
        if not isinstance(candidates, list):
            raise ValueError(
                f"GNN specialist {normalized} candidates must be an array"
            )
        tracks.append(SpecialistTrackPolicy(
            key=normalized,
            enabled=bool(raw.get("enabled", False)),
            candidate_architectures=tuple(
                dict.fromkeys(str(value).strip().lower() for value in candidates)
            ),
            feature_contract=str(raw["feature_contract"]).strip().lower(),
            minimum_train_positives=int(raw["minimum_train_positives"]),
            minimum_train_negatives=int(raw["minimum_train_negatives"]),
            minimum_holdout_positives=int(raw["minimum_holdout_positives"]),
            minimum_holdout_negatives=int(raw["minimum_holdout_negatives"]),
            minimum_evaluable_temporal_folds=int(
                raw["minimum_evaluable_temporal_folds"]
            ),
            maximum_fold_pr_auc_range=float(raw["maximum_fold_pr_auc_range"]),
            minimum_improvement_over_mlp=float(
                raw["minimum_improvement_over_mlp"]
            ),
            maximum_holdout_brier_score=float(
                raw["maximum_holdout_brier_score"]
            ),
            maximum_holdout_inference_ms=float(
                raw["maximum_holdout_inference_ms"]
            ),
            incumbent_refresh_tolerance=float(
                raw["incumbent_refresh_tolerance"]
            ),
            architecture_switch_tolerance=float(
                raw["architecture_switch_tolerance"]
            ),
        ))
    return tuple(tracks)


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
    schema_version = configuration.get("schema_version") if isinstance(configuration, dict) else None
    if schema_version not in {1, 3}:
        raise ValueError("GNN ConfigurationJson must use schema_version 1 or 3")
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
        aggregation = configuration.get("aggregation") or {}
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
            serving_mode=str(
                configuration.get("serving_mode", SERVING_MODE_V2)
            ).strip().lower(),
            specialist_tracks=(
                _specialist_tracks(configuration) if schema_version == 3 else ()
            ),
            aggregation_method=str(
                aggregation.get("method", "maximum_calibrated_probability")
            ).strip().lower(),
            mixed_minimum_specialists=int(
                aggregation.get("mixed_minimum_specialists", 2)
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("GNN ConfigurationJson contains invalid settings") from exc
    _validate(policy)
    return policy
