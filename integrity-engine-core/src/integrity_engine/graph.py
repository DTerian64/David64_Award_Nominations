"""Versioned Graph snapshot contract and candidate-aware detector evaluation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import heapq
from typing import Any, Iterable, Mapping

from .scoring import continuous_score, risk_level


GRAPH_SNAPSHOT_SCHEMA_VERSION = 1
BEHAVIOR_STATUSES = ("Pending", "Approved", "Paid")


class EvaluationLimitExceeded(RuntimeError):
    """The bounded candidate search could not finish within its work budget."""


def _as_utc(value: date | datetime | str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    else:
        text = str(value).replace("Z", "+00:00")
        result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class SnapshotNomination:
    nomination_id: int
    nominator_id: int
    beneficiary_id: int
    amount: float
    status: str
    created_at: datetime

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SnapshotNomination":
        return cls(
            nomination_id=int(value["nomination_id"]),
            nominator_id=int(value["nominator_id"]),
            beneficiary_id=int(value["beneficiary_id"]),
            amount=float(value.get("amount") or 0.0),
            status=str(value["status"]),
            created_at=_as_utc(value["created_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nomination_id": self.nomination_id,
            "nominator_id": self.nominator_id,
            "beneficiary_id": self.beneficiary_id,
            "amount": self.amount,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class CandidateNomination:
    nomination_id: int
    nominator_id: int
    beneficiary_id: int
    amount: float
    created_at: datetime

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateNomination":
        created_at = value.get("nomination_date") or value.get("created_at")
        if created_at is None:
            raise ValueError("Candidate nomination is missing its evaluation time")
        return cls(
            nomination_id=int(value["nomination_id"]),
            nominator_id=int(value["nominator_id"]),
            beneficiary_id=int(value["beneficiary_id"]),
            amount=float(value.get("amount") or 0.0),
            created_at=_as_utc(created_at),
        )


@dataclass(frozen=True)
class GraphInferenceSnapshot:
    tenant_id: int
    run_id: str
    policy_version: int
    generated_at: datetime
    window_days: int
    scoring_policy: Mapping[str, Any]
    nominations: tuple[SnapshotNomination, ...]
    schema_version: int = GRAPH_SNAPSHOT_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GraphInferenceSnapshot":
        schema_version = int(value.get("schema_version", 0))
        if schema_version != GRAPH_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported Graph snapshot schema: {schema_version}")
        statuses = tuple(value.get("behavior_statuses") or ())
        if statuses != BEHAVIOR_STATUSES:
            raise ValueError(f"Unsupported Graph behavior statuses: {statuses!r}")
        nominations = tuple(
            SnapshotNomination.from_dict(item)
            for item in value.get("nominations") or []
        )
        if len({item.nomination_id for item in nominations}) != len(nominations):
            raise ValueError("Graph snapshot contains duplicate nomination IDs")
        return cls(
            tenant_id=int(value["tenant_id"]),
            run_id=str(value["run_id"]),
            policy_version=int(value["policy_version"]),
            generated_at=_as_utc(value["generated_at"]),
            window_days=int(value["window_days"]),
            scoring_policy=value["scoring_policy"],
            nominations=nominations,
            schema_version=schema_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "policy_version": self.policy_version,
            "generated_at": self.generated_at.isoformat(),
            "window_days": self.window_days,
            "behavior_statuses": list(BEHAVIOR_STATUSES),
            "scoring_policy": self.scoring_policy,
            "nominations": [item.to_dict() for item in self.nominations],
        }


@dataclass(frozen=True)
class RingEvaluation:
    detector: str
    evaluation_mode: str
    evidence_scope: str
    score: float
    severity: str
    score_components: Mapping[str, Any]
    affected_user_ids: tuple[int, ...]
    supporting_nomination_ids: tuple[int, ...]
    total_amount: float
    candidate_nomination_id: int
    path_user_ids: tuple[int, ...]
    paths_considered: int
    states_visited: int
    states_generated: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "evaluation_mode": self.evaluation_mode,
            "evidence_scope": self.evidence_scope,
            "score": self.score,
            "severity": self.severity,
            "score_components": dict(self.score_components),
            "affected_user_ids": list(self.affected_user_ids),
            "supporting_nomination_ids": list(self.supporting_nomination_ids),
            "total_amount": self.total_amount,
            "candidate_nomination_id": self.candidate_nomination_id,
            "path_user_ids": list(self.path_user_ids),
            "paths_considered": self.paths_considered,
            "states_visited": self.states_visited,
            "states_generated": self.states_generated,
        }


def _ring_config(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    patterns = policy.get("patterns") or {}
    config = patterns.get("Ring") or {}
    if not config:
        raise ValueError("Graph scoring policy has no Ring configuration")
    return config


def evaluate_ring_candidate(
    snapshot: GraphInferenceSnapshot,
    candidate: CandidateNomination,
    *,
    max_states: int = 100_000,
    max_ring_size: int = 8,
) -> RingEvaluation | None:
    """Return the best ring completed by the candidate edge.

    A ring of 3..8 users exists when the historical graph contains a simple
    path beneficiary -> ... -> nominator with 2..7 edges.  Historical edges at
    or after the candidate time and the candidate nomination itself are
    excluded, making retries deterministic and preventing future leakage.
    """
    if snapshot.tenant_id <= 0 or candidate.nominator_id == candidate.beneficiary_id:
        return None
    ring = _ring_config(snapshot.scoring_policy)
    if not bool(ring.get("enabled", True)):
        return None

    edge_items: dict[tuple[int, int], list[SnapshotNomination]] = defaultdict(list)
    adjacency: dict[int, set[int]] = defaultdict(set)
    for item in snapshot.nominations:
        if (
            item.nomination_id == candidate.nomination_id
            or item.created_at >= candidate.created_at
            or item.status not in BEHAVIOR_STATUSES
        ):
            continue
        key = (item.nominator_id, item.beneficiary_id)
        edge_items[key].append(item)
        adjacency[item.nominator_id].add(item.beneficiary_id)

    edge_amount = {
        key: sum(item.amount for item in items)
        for key, items in edge_items.items()
    }
    edge_nomination_count = {key: len(items) for key, items in edge_items.items()}
    maximum_edge_amount = max(edge_amount.values(), default=0.0)
    maximum_edge_count = max(edge_nomination_count.values(), default=0)

    start = candidate.beneficiary_id
    target = candidate.nominator_id
    max_historical_edges = max(2, min(max_ring_size - 1, 7))
    best: tuple[tuple, RingEvaluation] | None = None
    states_visited = 0
    states_generated = 1
    paths_considered = 0

    parameters = ring.get("parameters") or {}
    amount_reference = max(float(parameters.get("amount_reference", 10_000)), 1.0)

    def score_for(path_size: int, total_amount: float, nomination_count: int):
        signals = {
            "exposure": min(total_amount / amount_reference, 1.0),
            "repeat": min(nomination_count / max(path_size * 3, 1), 1.0),
            "compactness": max(0.0, 1.0 - ((path_size - 3) / 5.0)),
        }
        return continuous_score(
            base_score=float(ring.get("base_score", 0.0)),
            minimum_score=float(ring.get("minimum_score", 0.0)),
            maximum_score=float(ring.get("maximum_score", 100.0)),
            parameters=parameters,
            signals=signals,
        )

    def upper_bound(path: tuple[int, ...], amount: float, count: int) -> float:
        used_edges = len(path) - 1
        remaining = max_historical_edges - used_edges
        # A completion needs at least one more edge and at least three users.
        shortest_size = max(3, len(path) + 1)
        possible_amount = amount + remaining * maximum_edge_amount
        possible_count = count + remaining * maximum_edge_count
        return score_for(shortest_size, possible_amount, possible_count)[0]

    initial_path = (start,)
    initial_amount = candidate.amount
    initial_count = 1
    queue: list[tuple[float, int, tuple[int, ...], float, int]] = [(
        -upper_bound(initial_path, initial_amount, initial_count),
        1,
        initial_path,
        initial_amount,
        initial_count,
    )]

    while queue:
        negative_bound, _path_length, path, accumulated_amount, accumulated_count = heapq.heappop(queue)
        if best is not None and -negative_bound < best[1].score:
            continue
        node = path[-1]
        states_visited += 1
        if states_visited > max_states:
            raise EvaluationLimitExceeded(
                f"Ring candidate search exceeded {max_states} states"
            )
        path_edge_count = len(path) - 1
        if node == target:
            if path_edge_count < 2:
                continue
            paths_considered += 1
            historical: list[SnapshotNomination] = []
            for source, destination in zip(path, path[1:]):
                historical.extend(edge_items[(source, destination)])
            nomination_ids = sorted(
                {item.nomination_id for item in historical} | {candidate.nomination_id}
            )
            total_amount = accumulated_amount
            size = len(path)
            score, components = score_for(size, total_amount, accumulated_count)
            thresholds = snapshot.scoring_policy["thresholds"]
            evaluation = RingEvaluation(
                detector="Ring",
                evaluation_mode="CANDIDATE_EDGE",
                evidence_scope="CURRENT_NOMINATION",
                score=score,
                severity=risk_level(score, thresholds),
                score_components=components,
                affected_user_ids=tuple(sorted(path)),
                supporting_nomination_ids=tuple(nomination_ids),
                total_amount=round(total_amount, 2),
                candidate_nomination_id=candidate.nomination_id,
                path_user_ids=tuple(path) + (start,),
                paths_considered=paths_considered,
                states_visited=states_visited,
                states_generated=states_generated,
            )
            # Highest score wins. Remaining fields make ties stable regardless
            # of set/dictionary iteration or source query ordering.
            rank = (-score, size, tuple(path), tuple(nomination_ids))
            if best is None or rank < best[0]:
                best = (rank, evaluation)
            continue
        if path_edge_count >= max_historical_edges:
            continue
        neighbors = sorted(
            adjacency.get(node, ()),
            key=lambda neighbor: (
                -edge_amount[(node, neighbor)],
                -edge_nomination_count[(node, neighbor)],
                neighbor,
            ),
        )
        for neighbor in neighbors:
            if neighbor in path:
                continue
            new_path = path + (neighbor,)
            new_amount = accumulated_amount + edge_amount[(node, neighbor)]
            new_count = accumulated_count + edge_nomination_count[(node, neighbor)]
            bound = upper_bound(new_path, new_amount, new_count)
            if best is not None and bound < best[1].score:
                continue
            states_generated += 1
            if states_generated > max_states:
                raise EvaluationLimitExceeded(
                    f"Ring candidate search exceeded {max_states} generated states"
                )
            heapq.heappush(
                queue,
                (-bound, len(new_path), new_path, new_amount, new_count),
            )

    if best is None:
        return None
    result = best[1]
    # Report totals for the complete search, not the moment the winner appeared.
    return RingEvaluation(
        **{
            **result.__dict__,
            "paths_considered": paths_considered,
            "states_visited": states_visited,
            "states_generated": states_generated,
        }
    )
