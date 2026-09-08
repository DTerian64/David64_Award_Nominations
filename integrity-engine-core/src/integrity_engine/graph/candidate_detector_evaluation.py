"""Candidate-aware scores for Graph detectors other than Ring.

The functions in this module deliberately separate a detector's continuous
numeric score from its routing eligibility.  ELCE can therefore inspect how a
candidate changes every detector while production routing still considers only
eligible, policy-enabled results.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from math import sqrt
from statistics import mean, median, pstdev
from typing import Any, Iterable, Mapping, Sequence

from .candidate_edge_evaluation import (
    BEHAVIOR_STATUSES,
    CandidateNomination,
    GraphInferenceSnapshot,
    SnapshotNomination,
)
from .finding_scoring import (
    calculate_graph_finding_score,
    derive_graph_finding_severity,
)


@dataclass(frozen=True)
class CandidateDetectorEvaluation:
    detector: str
    score: float
    severity: str
    eligible: bool
    enabled_for_routing: bool
    state: str
    eligibility_reasons: tuple[str, ...]
    score_components: Mapping[str, Any]
    affected_user_ids: tuple[int, ...]
    supporting_nomination_ids: tuple[int, ...]
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "score": self.score,
            "severity": self.severity,
            "eligible": self.eligible,
            "enabled_for_routing": self.enabled_for_routing,
            "state": self.state,
            "eligibility_reasons": list(self.eligibility_reasons),
            "score_components": dict(self.score_components),
            "affected_user_ids": list(self.affected_user_ids),
            "supporting_nomination_ids": list(self.supporting_nomination_ids),
            "detail": self.detail,
        }


def _config(snapshot: GraphInferenceSnapshot, detector: str) -> Mapping[str, Any]:
    return (snapshot.scoring_policy.get("patterns") or {}).get(detector) or {}


def _score(
    snapshot: GraphInferenceSnapshot,
    detector: str,
    signals: Mapping[str, float],
) -> tuple[float, str, dict[str, Any]]:
    config = _config(snapshot, detector)
    score, components = calculate_graph_finding_score(
        base_score=float(config.get("base_score", 0)),
        minimum_score=float(config.get("minimum_score", 0)),
        maximum_score=float(config.get("maximum_score", 100)),
        parameters=config.get("parameters") or {},
        signals=signals,
    )
    severity = derive_graph_finding_severity(
        score, snapshot.scoring_policy["thresholds"]
    )
    return score, severity, components


def _state(config: Mapping[str, Any], eligible: bool) -> str:
    if not bool(config.get("enabled_for_routing", False)):
        return "ANALYTICS_ONLY"
    return "SCORING" if eligible else "NOT_SCORING"


def _evaluation(
    snapshot: GraphInferenceSnapshot,
    detector: str,
    *,
    signals: Mapping[str, float],
    eligible: bool,
    reasons: Iterable[str],
    affected_users: Iterable[int],
    nominations: Iterable[SnapshotNomination],
    detail: str,
) -> CandidateDetectorEvaluation:
    config = _config(snapshot, detector)
    score, severity, components = _score(snapshot, detector, signals)
    return CandidateDetectorEvaluation(
        detector=detector,
        score=score,
        severity=severity,
        eligible=eligible,
        enabled_for_routing=bool(config.get("enabled_for_routing", False)),
        state=_state(config, eligible),
        eligibility_reasons=tuple(reasons),
        score_components=components,
        affected_user_ids=tuple(sorted(set(affected_users))),
        supporting_nomination_ids=tuple(sorted({item.nomination_id for item in nominations})),
        detail=detail,
    )


def evaluate_no_finding(
    snapshot: GraphInferenceSnapshot,
    detector: str,
    reason: str,
) -> CandidateDetectorEvaluation:
    """Return the detector's zero-signal formula result without routing it."""
    return _evaluation(
        snapshot,
        detector,
        signals={},
        eligible=False,
        reasons=[reason],
        affected_users=[],
        nominations=[],
        detail=reason,
    )


def _candidate_record(candidate: CandidateNomination) -> SnapshotNomination:
    return SnapshotNomination(
        nomination_id=candidate.nomination_id,
        nominator_id=candidate.nominator_id,
        beneficiary_id=candidate.beneficiary_id,
        amount=candidate.amount,
        status="Pending",
        created_at=candidate.created_at,
        description=candidate.description,
    )


def _candidate_graph(
    snapshot: GraphInferenceSnapshot, candidate: CandidateNomination
) -> list[SnapshotNomination]:
    history = [
        item for item in snapshot.nominations
        if item.nomination_id != candidate.nomination_id
        and item.created_at < candidate.created_at
        and item.status in BEHAVIOR_STATUSES
    ]
    history.append(_candidate_record(candidate))
    return history


def evaluate_super_nominator(
    snapshot: GraphInferenceSnapshot, candidate: CandidateNomination
) -> CandidateDetectorEvaluation:
    detector = "SuperNominator"
    config = _config(snapshot, detector)
    parameters = config.get("parameters") or {}
    records = _candidate_graph(snapshot, candidate)
    outgoing: dict[int, list[SnapshotNomination]] = defaultdict(list)
    for item in records:
        outgoing[item.nominator_id].append(item)
    counts = [len(items) for items in outgoing.values()]
    target = outgoing[candidate.nominator_id]
    count = len(target)
    threshold = max(
        (mean(counts) if counts else 0)
        + float(parameters.get("standard_deviations", 2.0))
        * (pstdev(counts) if len(counts) > 1 else 0),
        float(parameters.get("median_multiplier", 3.0))
        * (median(counts) if counts else 0),
        float(parameters.get("minimum_count", 5)),
    )
    total_amount = sum(item.amount for item in target)
    eligible = len(outgoing) >= 3 and count >= threshold
    reasons = []
    if len(outgoing) < 3:
        reasons.append(f"population {len(outgoing)}/3 nominators")
    if count < threshold:
        reasons.append(f"nominations {count}/{threshold:.1f}")
    return _evaluation(
        snapshot, detector,
        signals={
            "excess": min(max((count / max(threshold, 1)) - 1, 0), 1),
            "volume": min(count / max(threshold * 2, 1), 1),
            "exposure": min(total_amount / max(float(
                parameters.get("amount_reference", 10_000)
            ), 1), 1),
        },
        eligible=eligible,
        reasons=reasons,
        affected_users=[candidate.nominator_id],
        nominations=target,
        detail=f"Nominator sent {count} nominations; eligibility threshold {threshold:.1f}.",
    )


def evaluate_super_beneficiary(
    snapshot: GraphInferenceSnapshot, candidate: CandidateNomination
) -> CandidateDetectorEvaluation:
    detector = "SuperBeneficiary"
    config = _config(snapshot, detector)
    parameters = config.get("parameters") or {}
    records = _candidate_graph(snapshot, candidate)
    incoming: dict[int, list[SnapshotNomination]] = defaultdict(list)
    for item in records:
        incoming[item.beneficiary_id].append(item)
    counts = [len(items) for items in incoming.values()]
    target = incoming[candidate.beneficiary_id]
    count = len(target)
    threshold = max(
        (mean(counts) if counts else 0)
        + float(parameters.get("standard_deviations", 2.0))
        * (pstdev(counts) if len(counts) > 1 else 0),
        float(parameters.get("median_multiplier", 3.0))
        * (median(counts) if counts else 0),
        float(parameters.get("minimum_count", 5)),
    )
    nominators = [item.nominator_id for item in target]
    unique_nominators = len(set(nominators))
    minimum_unique = int(parameters.get("minimum_unique_nominators", 4))
    dates = [item.created_at.date() for item in target]
    span_days = (max(dates) - min(dates)).days + 1 if dates else 0
    compactness_reference = max(float(
        parameters.get("compactness_reference_days", 14)
    ), 1)
    total_amount = sum(item.amount for item in target)
    dominant_count = max(Counter(nominators).values(), default=0)
    concentration_floor = 1 / unique_nominators if unique_nominators else 1
    dominant_share = dominant_count / count if count else 0
    repeat_concentration = max(0, min(
        (dominant_share - concentration_floor)
        / max(1 - concentration_floor, 0.001),
        1,
    ))
    eligible = (
        len(incoming) >= 3
        and count >= threshold
        and unique_nominators >= minimum_unique
    )
    reasons = []
    if len(incoming) < 3:
        reasons.append(f"population {len(incoming)}/3 beneficiaries")
    if count < threshold:
        reasons.append(f"nominations received {count}/{threshold:.1f}")
    if unique_nominators < minimum_unique:
        reasons.append(f"unique nominators {unique_nominators}/{minimum_unique}")
    return _evaluation(
        snapshot, detector,
        signals={
            "excess": min(max((count / max(threshold, 1)) - 1, 0), 1),
            "breadth": min(unique_nominators / max(float(
                parameters.get("unique_reference", 10)
            ), 1), 1),
            "repeat_concentration": repeat_concentration,
            "compactness": max(0, 1 - ((span_days - 1) / compactness_reference))
            if span_days else 0,
            "exposure": min(total_amount / max(float(
                parameters.get("amount_reference", 10_000)
            ), 1), 1),
        },
        eligible=eligible,
        reasons=reasons,
        affected_users=[candidate.beneficiary_id],
        nominations=target,
        detail=(
            f"Beneficiary received {count} nominations from {unique_nominators} "
            f"unique nominators; thresholds {threshold:.1f} and {minimum_unique}."
        ),
    )


def evaluate_temporal_burst(
    snapshot: GraphInferenceSnapshot, candidate: CandidateNomination
) -> CandidateDetectorEvaluation:
    detector = "TemporalBurst"
    config = _config(snapshot, detector)
    parameters = config.get("parameters") or {}
    records = _candidate_graph(snapshot, candidate)
    burst_days = max(int(parameters.get("burst_window_days", 3)), 1)
    baseline_days = max(int(parameters.get("minimum_baseline_days", 21)), burst_days)
    minimum_count = max(int(parameters.get("minimum_nominations", 8)), 1)
    standard_deviations = float(parameters.get("standard_deviations", 3.0))
    first_date = min(item.created_at.date() for item in records)
    last_date = max(item.created_at.date() for item in records)
    observed_days = (last_date - first_date).days + 1
    by_date: dict[Any, list[SnapshotNomination]] = defaultdict(list)
    for item in records:
        by_date[item.created_at.date()].append(item)
    starts = [
        first_date + timedelta(days=offset)
        for offset in range(max(observed_days - burst_days + 1, 1))
    ]
    rolling_counts = [
        sum(len(by_date.get(start + timedelta(days=offset), ()))
            for offset in range(burst_days))
        for start in starts
    ]
    expected = float(median(rolling_counts)) if rolling_counts else 0
    mad = float(median(abs(value - expected) for value in rolling_counts)) if rolling_counts else 0
    robust_deviation = max(1.4826 * mad, sqrt(max(expected, 1)))
    threshold = max(minimum_count, expected + standard_deviations * robust_deviation)
    candidate_date = candidate.created_at.date()
    candidate_windows = [
        (start, [
            item for offset in range(burst_days)
            for item in by_date.get(start + timedelta(days=offset), ())
        ])
        for start in starts
        if start <= candidate_date <= start + timedelta(days=burst_days - 1)
    ]
    start, target = max(candidate_windows, key=lambda pair: len(pair[1]))
    count = len(target)
    nominators = [item.nominator_id for item in target]
    beneficiaries = [item.beneficiary_id for item in target]
    total_amount = sum(item.amount for item in target)
    daily_peak = max(
        len(by_date.get(start + timedelta(days=offset), ()))
        for offset in range(burst_days)
    )
    participant_counts = Counter(nominators + beneficiaries)
    concentration = max(participant_counts.values(), default=0) / max(count, 1)
    eligible = observed_days >= baseline_days and count >= threshold
    reasons = []
    if observed_days < baseline_days:
        reasons.append(f"baseline {observed_days}/{baseline_days} days")
    if count < threshold:
        reasons.append(f"window nominations {count}/{threshold:.1f}")
    return _evaluation(
        snapshot, detector,
        signals={
            "excess": min(max((count / max(threshold, 1)) - 1, 0), 1),
            "volume": min(count / max(float(
                parameters.get("count_reference", 20)
            ), 1), 1),
            "participant_concentration": concentration,
            "temporal_compactness": min(daily_peak / max(count, 1), 1),
            "exposure": min(total_amount / max(float(
                parameters.get("amount_reference", 10_000)
            ), 1), 1),
        },
        eligible=eligible,
        reasons=reasons,
        affected_users=set(nominators) | set(beneficiaries),
        nominations=target,
        detail=(
            f"Candidate window contains {count} nominations; eligibility "
            f"threshold {threshold:.1f}."
        ),
    )


def _jaccard(left: set[int], right: set[int]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _candidate_dense_group(
    records: Sequence[SnapshotNomination],
    candidate: CandidateNomination,
    parameters: Mapping[str, Any],
) -> tuple[set[int], set[int], list[SnapshotNomination], float, float]:
    outgoing: dict[int, set[int]] = defaultdict(set)
    incoming: dict[int, set[int]] = defaultdict(set)
    for item in records:
        outgoing[item.nominator_id].add(item.beneficiary_id)
        incoming[item.beneficiary_id].add(item.nominator_id)
    minimum_shared = max(int(parameters.get("minimum_shared_neighbors", 2)), 1)
    overlap_threshold = float(parameters.get("overlap_threshold", 0.6))
    minimum_density = float(parameters.get("minimum_density", 0.65))

    left_seed = candidate.nominator_id
    left_group = {
        user_id for user_id, neighbors in outgoing.items()
        if len(neighbors & outgoing[left_seed]) >= minimum_shared
        and _jaccard(neighbors, outgoing[left_seed]) >= overlap_threshold
    } | {left_seed}
    right_group = {
        beneficiary for beneficiary in set().union(
            *(outgoing[user] for user in left_group)
        )
        if sum(beneficiary in outgoing[user] for user in left_group)
        / max(len(left_group), 1) >= minimum_density
    } | {candidate.beneficiary_id}

    right_seed = candidate.beneficiary_id
    reverse_right = {
        user_id for user_id, neighbors in incoming.items()
        if len(neighbors & incoming[right_seed]) >= minimum_shared
        and _jaccard(neighbors, incoming[right_seed]) >= overlap_threshold
    } | {right_seed}
    reverse_left = {
        nominator for nominator in set().union(
            *(incoming[user] for user in reverse_right)
        )
        if sum(nominator in incoming[user] for user in reverse_right)
        / max(len(reverse_right), 1) >= minimum_density
    } | {candidate.nominator_id}

    candidates = [(left_group, right_group), (reverse_left, reverse_right)]
    best = None
    for left, right in candidates:
        items = [
            item for item in records
            if item.nominator_id in left and item.beneficiary_id in right
        ]
        edges = {(item.nominator_id, item.beneficiary_id) for item in items}
        density = len(edges) / max(len(left) * len(right), 1)
        overlap_values = [
            _jaccard(outgoing[user], right) for user in left
        ] + [
            _jaccard(incoming[user], left) for user in right
        ]
        overlap = mean(overlap_values) if overlap_values else 0.0
        key = (density, len(edges), len(items))
        if best is None or key > best[0]:
            best = (key, left, right, items, density, overlap)
    assert best is not None
    return best[1], best[2], best[3], best[4], best[5]


def evaluate_bipartite_dense_block(
    snapshot: GraphInferenceSnapshot, candidate: CandidateNomination
) -> CandidateDetectorEvaluation:
    detector = "BipartiteDenseBlock"
    config = _config(snapshot, detector)
    parameters = config.get("parameters") or {}
    records = _candidate_graph(snapshot, candidate)
    left, right, target, density, overlap = _candidate_dense_group(
        records, candidate, parameters
    )
    edges = {(item.nominator_id, item.beneficiary_id) for item in target}
    minimum_side = max(int(parameters.get("minimum_side_size", 2)), 2)
    minimum_large_side = max(
        int(parameters.get("minimum_large_side_size", 3)), minimum_side
    )
    minimum_edges = max(int(parameters.get("minimum_edges", 6)), 1)
    minimum_density = float(parameters.get("minimum_density", 0.65))
    repeat_rate = max((len(target) / max(len(edges), 1)) - 1, 0)
    exclusivity_values = [
        sum(item.nominator_id == user and item.beneficiary_id in right for item in records)
        / max(sum(item.nominator_id == user for item in records), 1)
        for user in left
    ] + [
        sum(item.beneficiary_id == user and item.nominator_id in left for item in records)
        / max(sum(item.beneficiary_id == user for item in records), 1)
        for user in right
    ]
    exclusivity = mean(exclusivity_values) if exclusivity_values else 0
    dates = [item.created_at.date() for item in target]
    span_days = (max(dates) - min(dates)).days + 1 if dates else 0
    compactness_reference = max(float(
        parameters.get("compactness_reference_days", 14)
    ), 1)
    total_amount = sum(item.amount for item in target)
    eligible = (
        len(left) >= minimum_side
        and len(right) >= minimum_side
        and max(len(left), len(right)) >= minimum_large_side
        and len(edges) >= minimum_edges
        and density >= minimum_density
    )
    reasons = []
    if len(left) < minimum_side or len(right) < minimum_side:
        reasons.append(
            f"block sides {len(left)}x{len(right)}; minimum {minimum_side}x{minimum_side}"
        )
    if max(len(left), len(right)) < minimum_large_side:
        reasons.append(f"largest side {max(len(left), len(right))}/{minimum_large_side}")
    if len(edges) < minimum_edges:
        reasons.append(f"distinct edges {len(edges)}/{minimum_edges}")
    if density < minimum_density:
        reasons.append(f"density {density:.3f}/{minimum_density:.3f}")
    return _evaluation(
        snapshot, detector,
        signals={
            "density": min(max(
                (density - minimum_density) / max(1 - minimum_density, 0.001), 0
            ), 1),
            "overlap": min(overlap, 1),
            "exclusivity": min(exclusivity, 1),
            "repeat": min(repeat_rate / max(float(
                parameters.get("repeat_reference", 2)
            ), 1), 1),
            "compactness": max(0, 1 - ((span_days - 1) / compactness_reference))
            if span_days else 0,
            "exposure": min(total_amount / max(float(
                parameters.get("amount_reference", 10_000)
            ), 1), 1),
        },
        eligible=eligible,
        reasons=reasons,
        affected_users=left | right,
        nominations=target,
        detail=(
            f"Candidate block is {len(left)}x{len(right)} with {len(edges)} "
            f"distinct edges and density {density:.3f}."
        ),
    )


def evaluate_copy_paste(
    snapshot: GraphInferenceSnapshot,
    candidate: CandidateNomination,
    *,
    component_nomination_ids: Iterable[int],
    qualifying_similarities: Iterable[float],
) -> CandidateDetectorEvaluation:
    """Score the exact similarity component created by the candidate text.

    Embedding generation is deliberately outside this package. Callers pass
    the connected component and all above-threshold edges, while this shared
    engine owns the policy gates and continuous score calculation.
    """
    detector = "CopyPaste"
    config = _config(snapshot, detector)
    parameters = config.get("parameters") or {}
    threshold = float(parameters.get("similarity_threshold", 0.92))
    minimum_size = max(int(parameters.get("minimum_cluster_size", 3)), 1)
    ids = {int(value) for value in component_nomination_ids}
    ids.add(candidate.nomination_id)
    records = [
        item for item in _candidate_graph(snapshot, candidate)
        if item.nomination_id in ids
        and item.description
        and len(item.description.strip()) > 20
    ]
    similarities = [
        float(value) for value in qualifying_similarities
        if float(value) >= threshold
    ]
    average_similarity = mean(similarities) if similarities else threshold
    total_amount = sum(item.amount for item in records)
    eligible = (
        len(candidate.description.strip()) > 20
        and len(records) >= minimum_size
        and bool(similarities)
    )
    reasons = []
    if len(candidate.description.strip()) <= 20:
        reasons.append("description must contain more than 20 characters")
    if len(records) < minimum_size:
        reasons.append(f"similar description cluster {len(records)}/{minimum_size}")
    if not similarities:
        reasons.append(f"no cosine similarity reached {threshold:.2f}")
    return _evaluation(
        snapshot,
        detector,
        signals={
            "similarity": min(max(
                (average_similarity - threshold) / max(1 - threshold, 0.001),
                0,
            ), 1),
            "cluster_size": min(len(records) / max(float(
                parameters.get("cluster_size_reference", 8)
            ), 1), 1),
            "exposure": min(total_amount / max(float(
                parameters.get("amount_reference", 10_000)
            ), 1), 1),
        },
        eligible=eligible,
        reasons=reasons,
        affected_users=[item.nominator_id for item in records],
        nominations=records,
        detail=(
            f"Candidate description forms a component of {len(records)} nominations "
            f"with average qualifying cosine similarity {average_similarity:.3f}."
        ),
    )


def evaluate_candidate_detectors(
    snapshot: GraphInferenceSnapshot,
    candidate: CandidateNomination,
    *,
    copy_paste_component_nomination_ids: Iterable[int] | None = None,
    copy_paste_qualifying_similarities: Iterable[float] | None = None,
) -> tuple[CandidateDetectorEvaluation, ...]:
    """Evaluate currently supported non-Ring candidate detectors."""
    evaluators = {
        "BipartiteDenseBlock": evaluate_bipartite_dense_block,
        "SuperNominator": evaluate_super_nominator,
        "SuperBeneficiary": evaluate_super_beneficiary,
        "TemporalBurst": evaluate_temporal_burst,
    }
    results = [
        evaluator(snapshot, candidate)
        for detector, evaluator in evaluators.items()
        if bool(_config(snapshot, detector).get("enabled", False))
    ]
    if (
        bool(_config(snapshot, "CopyPaste").get("enabled", False))
        and copy_paste_component_nomination_ids is not None
        and copy_paste_qualifying_similarities is not None
    ):
        results.append(evaluate_copy_paste(
            snapshot,
            candidate,
            component_nomination_ids=copy_paste_component_nomination_ids,
            qualifying_similarities=copy_paste_qualifying_similarities,
        ))
    return tuple(results)
