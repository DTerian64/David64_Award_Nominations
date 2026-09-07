"""Versioned Graph snapshot contract and candidate-edge detector evaluation."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import heapq
from typing import Any, Mapping

from .finding_scoring import (
    calculate_graph_finding_score,
    derive_graph_finding_severity,
)


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
    search_status: str = "COMPLETE"
    search_complete: bool = True
    score_semantics: str = "EXACT"
    configured_max_states: int = 100_000
    configured_max_ring_size: int = 8
    limit_strategy: str = "BEST_EVIDENCE"
    pruned_unreachable: int = 0
    pruned_by_bound: int = 0
    pruned_by_dominance: int = 0
    remaining_score_upper_bound: float | None = None

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
            "search_status": self.search_status,
            "search_complete": self.search_complete,
            "score_semantics": self.score_semantics,
            "configured_max_states": self.configured_max_states,
            "configured_max_ring_size": self.configured_max_ring_size,
            "limit_strategy": self.limit_strategy,
            "pruned_unreachable": self.pruned_unreachable,
            "pruned_by_bound": self.pruned_by_bound,
            "pruned_by_dominance": self.pruned_by_dominance,
            "remaining_score_upper_bound": self.remaining_score_upper_bound,
        }


def _ring_config(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    patterns = policy.get("patterns") or {}
    config = patterns.get("Ring") or {}
    if not config:
        raise ValueError("Graph scoring policy has no Ring configuration")
    return config


def evaluate_candidate_edge_for_ring(
    snapshot: GraphInferenceSnapshot,
    candidate: CandidateNomination,
    *,
    max_states: int | None = None,
    max_ring_size: int | None = None,
    require_complete: bool = False,
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
    candidate_policy = ring.get("candidate_evaluation") or {}
    configured_max_states = int(
        max_states
        if max_states is not None
        else candidate_policy.get("max_states", 100_000)
    )
    configured_max_ring_size = int(
        max_ring_size
        if max_ring_size is not None
        else candidate_policy.get("max_ring_size", 8)
    )
    limit_strategy = str(
        candidate_policy.get("limit_strategy", "BEST_EVIDENCE")
    ).upper()
    if configured_max_states <= 0:
        raise ValueError("Ring candidate max_states must be positive")
    if not 3 <= configured_max_ring_size <= 8:
        raise ValueError("Ring candidate max_ring_size must be between 3 and 8")
    if limit_strategy != "BEST_EVIDENCE":
        raise ValueError(f"Unsupported Ring limit strategy: {limit_strategy}")

    edge_items: dict[tuple[int, int], list[SnapshotNomination]] = defaultdict(list)
    adjacency: dict[int, set[int]] = defaultdict(set)
    reverse_adjacency: dict[int, set[int]] = defaultdict(set)
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
        reverse_adjacency[item.beneficiary_id].add(item.nominator_id)

    edge_amount = {
        key: sum(item.amount for item in items)
        for key, items in edge_items.items()
    }
    edge_nomination_count = {key: len(items) for key, items in edge_items.items()}
    start = candidate.beneficiary_id
    target = candidate.nominator_id
    max_historical_edges = configured_max_ring_size - 1
    best: tuple[tuple, RingEvaluation] | None = None
    states_visited = 0
    states_generated = 1
    paths_considered = 0
    pruned_unreachable = 0
    pruned_by_bound = 0
    pruned_by_dominance = 0

    parameters = ring.get("parameters") or {}
    amount_reference = max(float(parameters.get("amount_reference", 10_000)), 1.0)

    def score_ring_path_finding(
        path_size: int,
        total_amount: float,
        nomination_count: int,
    ):
        signals = {
            "exposure": min(total_amount / amount_reference, 1.0),
            "repeat": min(nomination_count / max(path_size * 3, 1), 1.0),
            "compactness": max(0.0, 1.0 - ((path_size - 3) / 5.0)),
        }
        return calculate_graph_finding_score(
            base_score=float(ring.get("base_score", 0.0)),
            minimum_score=float(ring.get("minimum_score", 0.0)),
            maximum_score=float(ring.get("maximum_score", 100.0)),
            parameters=parameters,
            signals=signals,
        )

    def shortest_distances(
        origin: int,
        graph: Mapping[int, set[int]],
    ) -> dict[int, int]:
        distances = {origin: 0}
        pending = deque([origin])
        while pending:
            node = pending.popleft()
            if distances[node] >= max_historical_edges:
                continue
            for neighbor in sorted(graph.get(node, ())):
                if neighbor not in distances:
                    distances[neighbor] = distances[node] + 1
                    pending.append(neighbor)
        return distances

    distance_from_start = shortest_distances(start, adjacency)
    distance_to_target = shortest_distances(target, reverse_adjacency)
    if target not in distance_from_start:
        return None

    corridor_nodes = {
        node for node, forward_distance in distance_from_start.items()
        if node in distance_to_target
        and forward_distance + distance_to_target[node] <= max_historical_edges
    }
    corridor_adjacency: dict[int, tuple[int, ...]] = {}
    for node in sorted(corridor_nodes):
        neighbors = tuple(
            neighbor for neighbor in sorted(adjacency.get(node, ()))
            if neighbor in corridor_nodes
            and distance_from_start[node] + 1 + distance_to_target[neighbor]
            <= max_historical_edges
        )
        corridor_adjacency[node] = neighbors
        pruned_unreachable += len(adjacency.get(node, ())) - len(neighbors)
    pruned_unreachable += sum(
        len(neighbors) for node, neighbors in adjacency.items()
        if node not in corridor_nodes
    )

    amount_bound_cache: dict[tuple[int, int], float | None] = {}
    count_bound_cache: dict[tuple[int, int], int | None] = {}

    def maximum_additional_metric(
        node: int,
        exact_edges: int,
        metric: Mapping[tuple[int, int], float | int],
        cache: dict,
    ):
        key = (node, exact_edges)
        if key in cache:
            return cache[key]
        if exact_edges == 0:
            result = 0 if node == target else None
        else:
            values = []
            for neighbor in corridor_adjacency.get(node, ()):
                remainder = maximum_additional_metric(
                    neighbor, exact_edges - 1, metric, cache
                )
                if remainder is not None:
                    values.append(metric[(node, neighbor)] + remainder)
            result = max(values) if values else None
        cache[key] = result
        return result

    def upper_bound(path: tuple[int, ...], amount: float, count: int) -> float:
        used_edges = len(path) - 1
        remaining = max_historical_edges - used_edges
        minimum_more = max(1, 2 - used_edges)
        bounds = []
        for additional_edges in range(minimum_more, remaining + 1):
            possible_amount = maximum_additional_metric(
                path[-1], additional_edges, edge_amount, amount_bound_cache
            )
            possible_count = maximum_additional_metric(
                path[-1], additional_edges, edge_nomination_count, count_bound_cache
            )
            if possible_amount is None or possible_count is None:
                continue
            ring_size = used_edges + additional_edges + 1
            bounds.append(score_ring_path_finding(
                ring_size,
                amount + float(possible_amount),
                count + int(possible_count),
            )[0])
        return max(bounds, default=float("-inf"))

    def build_evaluation(path: tuple[int, ...]) -> RingEvaluation:
        nonlocal paths_considered
        paths_considered += 1
        historical: list[SnapshotNomination] = []
        for source, destination in zip(path, path[1:]):
            historical.extend(edge_items[(source, destination)])
        nomination_ids = tuple(sorted(
            {item.nomination_id for item in historical} | {candidate.nomination_id}
        ))
        total_amount = candidate.amount + sum(item.amount for item in historical)
        nomination_count = 1 + len(historical)
        score, components = score_ring_path_finding(
            len(path), total_amount, nomination_count
        )
        return RingEvaluation(
            detector="Ring",
            evaluation_mode="CANDIDATE_EDGE",
            evidence_scope="CURRENT_NOMINATION",
            score=score,
            severity=derive_graph_finding_severity(
                score, snapshot.scoring_policy["thresholds"]
            ),
            score_components=components,
            affected_user_ids=tuple(sorted(path)),
            supporting_nomination_ids=nomination_ids,
            total_amount=round(total_amount, 2),
            candidate_nomination_id=candidate.nomination_id,
            path_user_ids=tuple(path) + (start,),
            paths_considered=0,
            states_visited=0,
            states_generated=0,
            search_status="COMPLETE",
            search_complete=True,
            score_semantics="EXACT",
            configured_max_states=configured_max_states,
            configured_max_ring_size=configured_max_ring_size,
            limit_strategy=limit_strategy,
            pruned_unreachable=0,
            pruned_by_bound=0,
            pruned_by_dominance=0,
            remaining_score_upper_bound=None,
        )

    def consider(path: tuple[int, ...]) -> None:
        nonlocal best
        evaluation = build_evaluation(path)
        rank = (
            -evaluation.score,
            len(path),
            tuple(path),
            evaluation.supporting_nomination_ids,
        )
        if best is None or rank < best[0]:
            best = (rank, evaluation)

    # Establish concrete evidence before the bounded optimization search.
    # Three-person rings are both important and cheap to enumerate.
    for middle in corridor_adjacency.get(start, ()):
        if middle not in (start, target) and target in corridor_adjacency.get(middle, ()):
            consider((start, middle, target))

    if best is None:
        # Remove direct reciprocity and find the deterministic shortest return
        # path. A shortest path is simple, so this proves a Ring exists without
        # enumerating the dense set of longer alternatives.
        pending = deque([(start, (start,))])
        seen = {start}
        while pending and best is None:
            node, path = pending.popleft()
            if len(path) - 1 >= max_historical_edges:
                continue
            for neighbor in corridor_adjacency.get(node, ()):
                if node == start and neighbor == target:
                    continue
                if neighbor in path:
                    continue
                new_path = path + (neighbor,)
                if neighbor == target:
                    consider(new_path)
                    break
                if neighbor not in seen:
                    seen.add(neighbor)
                    pending.append((neighbor, new_path))

    if best is None:
        # Reachability may be supplied only by a two-person reciprocal edge,
        # which is intentionally not a Ring.
        return None

    maximum_score = float(ring.get("maximum_score", 100.0))
    if best[1].score >= maximum_score:
        result = best[1]
        return RingEvaluation(**{
            **result.__dict__,
            "paths_considered": paths_considered,
            "states_visited": states_visited,
            "states_generated": states_generated,
            "pruned_unreachable": pruned_unreachable,
        })

    initial_path = (start,)
    initial_amount = candidate.amount
    initial_count = 1
    initial_bound = upper_bound(initial_path, initial_amount, initial_count)
    queue: list[tuple[float, int, float, int, tuple[int, ...], float, int]] = [(
        -initial_bound,
        1,
        -initial_amount,
        -initial_count,
        initial_path,
        initial_amount,
        initial_count,
    )]
    dominance: dict[tuple[int, int, frozenset[int]], list[tuple[float, int, tuple[int, ...]]]] = {}
    limited = False
    remaining_upper_bound: float | None = None

    while queue:
        (
            negative_bound, _path_length, _negative_amount, _negative_count,
            path, accumulated_amount, accumulated_count,
        ) = heapq.heappop(queue)
        branch_bound = -negative_bound
        if best is not None and branch_bound <= best[1].score:
            pruned_by_bound += 1
            continue
        node = path[-1]
        states_visited += 1
        path_edge_count = len(path) - 1
        if node == target:
            if path_edge_count < 2:
                continue
            consider(path)
            if best[1].score >= maximum_score:
                queue.clear()
            continue
        if path_edge_count >= max_historical_edges:
            continue
        neighbors = sorted(
            corridor_adjacency.get(node, ()),
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
            new_depth = len(new_path) - 1
            if new_depth + distance_to_target.get(neighbor, max_historical_edges + 1) > max_historical_edges:
                pruned_unreachable += 1
                continue
            new_amount = accumulated_amount + edge_amount[(node, neighbor)]
            new_count = accumulated_count + edge_nomination_count[(node, neighbor)]
            if neighbor == target:
                if new_depth >= 2:
                    consider(new_path)
                    if best[1].score >= maximum_score:
                        queue.clear()
                        break
                continue
            bound = upper_bound(new_path, new_amount, new_count)
            if best is not None and bound <= best[1].score:
                pruned_by_bound += 1
                continue
            dominance_key = (neighbor, len(new_path), frozenset(new_path))
            prior_states = dominance.setdefault(dominance_key, [])
            if any(
                prior_amount >= new_amount and prior_count >= new_count
                for prior_amount, prior_count, _prior_path in prior_states
            ):
                pruned_by_dominance += 1
                continue
            dominance[dominance_key] = [
                prior for prior in prior_states
                if not (
                    new_amount >= prior[0] and new_count >= prior[1]
                    and (new_amount > prior[0] or new_count > prior[1])
                )
            ] + [(new_amount, new_count, new_path)]
            if states_generated >= configured_max_states:
                limited = True
                remaining_upper_bound = max(
                    bound,
                    -queue[0][0] if queue else float("-inf"),
                )
                break
            states_generated += 1
            heapq.heappush(
                queue,
                (
                    -bound, len(new_path), -new_amount, -new_count,
                    new_path, new_amount, new_count,
                ),
            )
        if limited:
            break

    if limited and require_complete:
        raise EvaluationLimitExceeded(
            f"Ring candidate search reached its {configured_max_states} state budget"
        )
    result = best[1]
    return RingEvaluation(
        **{
            **result.__dict__,
            "paths_considered": paths_considered,
            "states_visited": states_visited,
            "states_generated": states_generated,
            "search_status": "BOUNDED" if limited else "COMPLETE",
            "search_complete": not limited,
            "score_semantics": "LOWER_BOUND" if limited else "EXACT",
            "configured_max_states": configured_max_states,
            "pruned_unreachable": pruned_unreachable,
            "pruned_by_bound": pruned_by_bound,
            "pruned_by_dominance": pruned_by_dominance,
            "remaining_score_upper_bound": (
                round(float(remaining_upper_bound), 2)
                if limited and remaining_upper_bound is not None
                else None
            ),
        }
    )
