"""Causal topology features shared by GNN training and live inference.

Every value is calculated from raw nomination edges that precede the target by
``(CreatedAt, NominationId)``. The target and all later edges are excluded.
This module deliberately knows nothing about RF, Graph Analytics, semantic
checks, model scores, or routing decisions.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
import math
from typing import Any


CAUSAL_FEATURE_SCHEMA_VERSION = "gnn-v2-causal-v1"

CAUSAL_CONTEXT_FEATURE_COLUMNS = (
    "LogPriorDirectedPairCount",
    "LogPriorReversePairCount",
    "LogReverseTwoHopPathCount",
    "LogNominatorOutgoingCount30d",
    "LogNominatorUniqueBeneficiaries30d",
    "LogBeneficiaryIncomingCount30d",
    "LogBeneficiaryUniqueNominators30d",
    "LogDirectedPairCount30d",
    "LogBeneficiaryIncomingCount1h",
    "LogEndpointEdgeCount1h",
)


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _identifier(value: Any) -> tuple[int, int | str]:
    if value is None:
        raise ValueError("Causal GNN context requires a stable NominationId")
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


def _order_key(row: Mapping[str, Any]) -> tuple[datetime, tuple[int, int | str]]:
    return _timestamp(row["CreatedAt"]), _identifier(row["NominationId"])


def _eligible(row: Mapping[str, Any]) -> bool:
    return bool(row.get("IsBehaviorEligible", True))


def causal_context_values(
    history_rows: Sequence[Mapping[str, Any]],
    target: Mapping[str, Any],
    *,
    window_days: int,
) -> dict[str, float]:
    """Return strictly-prior causal topology values for one target edge."""
    vector = causal_context_matrix(
        history_rows,
        [target],
        window_days=window_days,
    )[0]
    return dict(zip(CAUSAL_CONTEXT_FEATURE_COLUMNS, vector))


def causal_context_matrix(
    history_rows: Sequence[Mapping[str, Any]],
    targets: Sequence[Mapping[str, Any]],
    *,
    window_days: int,
) -> list[list[float]]:
    """Return causal vectors in target order and canonical column order.

    The implementation replays the ordered edge stream once and maintains
    bounded 365-day, 30-day, and one-hour counters. This keeps weekly training
    linearithmic instead of comparing every target with every historical row.
    """
    if window_days < 1:
        raise ValueError("window_days must be at least 1")
    if not targets:
        return []

    ordered_history = sorted(
        (row for row in history_rows if _eligible(row)), key=_order_key
    )
    indexed_targets = sorted(
        enumerate(targets), key=lambda item: _order_key(item[1])
    )
    result: list[list[float] | None] = [None] * len(targets)

    full_rows: deque[tuple[datetime, int, int]] = deque()
    recent_rows: deque[tuple[datetime, int, int]] = deque()
    hour_rows: deque[tuple[datetime, int, int]] = deque()
    full_pairs: Counter[tuple[int, int]] = Counter()
    out_neighbors: dict[int, set[int]] = defaultdict(set)
    in_neighbors: dict[int, set[int]] = defaultdict(set)
    recent_pairs: Counter[tuple[int, int]] = Counter()
    recent_out: Counter[int] = Counter()
    recent_in: Counter[int] = Counter()
    recent_out_partners: Counter[tuple[int, int]] = Counter()
    recent_in_partners: Counter[tuple[int, int]] = Counter()
    recent_unique_out: Counter[int] = Counter()
    recent_unique_in: Counter[int] = Counter()
    hour_in: Counter[int] = Counter()
    hour_degree: Counter[int] = Counter()
    hour_undirected: Counter[frozenset[int]] = Counter()

    def add(row: Mapping[str, Any]) -> None:
        when = _timestamp(row["CreatedAt"])
        source = int(row["NominatorId"])
        destination = int(row["BeneficiaryId"])
        edge = (when, source, destination)
        full_rows.append(edge)
        recent_rows.append(edge)
        hour_rows.append(edge)
        full_pairs[source, destination] += 1
        out_neighbors[source].add(destination)
        in_neighbors[destination].add(source)
        recent_pairs[source, destination] += 1
        recent_out[source] += 1
        recent_in[destination] += 1
        if recent_out_partners[source, destination] == 0:
            recent_unique_out[source] += 1
        if recent_in_partners[source, destination] == 0:
            recent_unique_in[destination] += 1
        recent_out_partners[source, destination] += 1
        recent_in_partners[source, destination] += 1
        hour_in[destination] += 1
        hour_degree[source] += 1
        hour_degree[destination] += 1
        hour_undirected[frozenset((source, destination))] += 1

    def expire_full(cutoff: datetime) -> None:
        while full_rows and full_rows[0][0] < cutoff:
            _when, source, destination = full_rows.popleft()
            full_pairs[source, destination] -= 1
            if full_pairs[source, destination] == 0:
                del full_pairs[source, destination]
                out_neighbors[source].discard(destination)
                in_neighbors[destination].discard(source)

    def expire_recent(cutoff: datetime) -> None:
        while recent_rows and recent_rows[0][0] < cutoff:
            _when, source, destination = recent_rows.popleft()
            recent_pairs[source, destination] -= 1
            recent_out[source] -= 1
            recent_in[destination] -= 1
            recent_out_partners[source, destination] -= 1
            recent_in_partners[source, destination] -= 1
            if recent_pairs[source, destination] == 0:
                del recent_pairs[source, destination]
            if recent_out_partners[source, destination] == 0:
                del recent_out_partners[source, destination]
                recent_unique_out[source] -= 1
            if recent_in_partners[source, destination] == 0:
                del recent_in_partners[source, destination]
                recent_unique_in[destination] -= 1

    def expire_hour(cutoff: datetime) -> None:
        while hour_rows and hour_rows[0][0] < cutoff:
            _when, source, destination = hour_rows.popleft()
            hour_in[destination] -= 1
            hour_degree[source] -= 1
            hour_degree[destination] -= 1
            pair = frozenset((source, destination))
            hour_undirected[pair] -= 1
            if hour_undirected[pair] == 0:
                del hour_undirected[pair]

    history_index = 0
    for output_index, target in indexed_targets:
        target_time, target_id = _order_key(target)
        while history_index < len(ordered_history):
            row = ordered_history[history_index]
            if _order_key(row) >= (target_time, target_id):
                break
            add(row)
            history_index += 1

        expire_full(target_time - timedelta(days=window_days))
        expire_recent(target_time - timedelta(days=30))
        expire_hour(target_time - timedelta(hours=1))

        nominator = int(target["NominatorId"])
        beneficiary = int(target["BeneficiaryId"])
        reverse_two_hop = len(
            out_neighbors.get(beneficiary, set())
            & in_neighbors.get(nominator, set())
            - {nominator, beneficiary}
        )
        between_endpoints = hour_undirected.get(
            frozenset((nominator, beneficiary)), 0
        )
        raw = (
            full_pairs[nominator, beneficiary],
            full_pairs[beneficiary, nominator],
            reverse_two_hop,
            recent_out[nominator],
            recent_unique_out[nominator],
            recent_in[beneficiary],
            recent_unique_in[beneficiary],
            recent_pairs[nominator, beneficiary],
            hour_in[beneficiary],
            hour_degree[nominator] + hour_degree[beneficiary] - between_endpoints,
        )
        result[output_index] = [math.log1p(float(value)) for value in raw]

    return [row for row in result if row is not None]
