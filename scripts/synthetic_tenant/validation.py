"""Fail-closed validation for the generated Synthetics Inc. logical corpus."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from .scenarios import (
    ACTIVE_USER_COUNT,
    CATEGORIES,
    CATEGORY_AMOUNT_BOUNDS,
    CONTROL_FAMILY_BY_VARIANT,
    CORPUS_USER_COUNT,
    DEPARTMENTS,
    FRAUD_PER_SEGMENT,
    NOMINATION_COUNT,
    NOMINATIONS_PER_SEGMENT,
    PATTERN_TAXONOMY_VERSION,
    SEGMENT_COUNT,
    SPECIALIST_FAMILIES,
    UPN_DOMAIN,
    SyntheticNomination,
    SyntheticUser,
)


def validate_corpus(
    users: list[SyntheticUser],
    nominations: list[SyntheticNomination],
    as_of: date,
) -> dict:
    errors: list[str] = []
    user_ids = {user.logical_id for user in users}
    upns = [user.upn for user in users]

    if len(users) != CORPUS_USER_COUNT:
        errors.append(f"expected {CORPUS_USER_COUNT} corpus users, found {len(users)}")
    if len(user_ids) != len(users):
        errors.append("logical user IDs are not unique")
    if len(set(upns)) != len(upns):
        errors.append("UPNs are not unique")
    if any(not upn.endswith(f"@{UPN_DOMAIN}") for upn in upns):
        errors.append("one or more corpus UPNs use the wrong domain")
    if any(user.account_enabled for user in users):
        errors.append("corpus Entra identities must be disabled")
    if set(user.department for user in users) != set(DEPARTMENTS):
        errors.append("not every approved department is represented")
    if sum(user.manager_logical_id is None for user in users) != 1:
        errors.append("the corpus hierarchy must have exactly one root")
    if any(
        user.manager_logical_id is not None and user.manager_logical_id not in user_ids
        for user in users
    ):
        errors.append("a manager reference points outside the corpus")

    if len(nominations) != NOMINATION_COUNT:
        errors.append(f"expected {NOMINATION_COUNT} nominations, found {len(nominations)}")
    nomination_ids = {row.logical_id for row in nominations}
    if len(nomination_ids) != len(nominations):
        errors.append("logical nomination IDs are not unique")
    if any(row.nominator_logical_id == row.beneficiary_logical_id for row in nominations):
        errors.append("self-nominations are present")
    for field in ("nominator_logical_id", "beneficiary_logical_id", "approver_logical_id"):
        if any(getattr(row, field) not in user_ids for row in nominations):
            errors.append(f"{field} contains a cross-corpus reference")

    fraud_count = SEGMENT_COUNT * sum(FRAUD_PER_SEGMENT.values())
    legitimate_count = NOMINATION_COUNT - fraud_count
    class_counts = Counter(row.training_disposition for row in nominations)
    if class_counts != Counter({"LEGITIMATE": legitimate_count, "FRAUD": fraud_count}):
        errors.append(
            f"expected {legitimate_count:,}/{fraud_count:,} class balance, "
            f"found {dict(class_counts)}"
        )
    if any(
        row.status == "Rejected" and row.training_disposition != "FRAUD"
        for row in nominations
    ):
        errors.append("a rejected synthetic nomination is not labeled FRAUD")
    if not any(
        row.status == "Rejected" and row.training_disposition == "FRAUD"
        for row in nominations
    ):
        errors.append("the corpus has no rejected historical fraud outcomes")
    if not any(
        row.status in ("Approved", "Paid") and row.training_disposition == "FRAUD"
        for row in nominations
    ):
        errors.append("the corpus has no retrospectively discovered fraud outcomes")
    for row in nominations:
        patterns = tuple(row.confirmed_patterns)
        if row.training_disposition == "FRAUD":
            if row.scenario_family not in SPECIALIST_FAMILIES:
                errors.append(
                    f"{row.logical_id} has unsupported fraud family "
                    f"{row.scenario_family}"
                )
            if patterns != (row.scenario_family,):
                errors.append(
                    f"{row.logical_id} pattern does not exactly match its family"
                )
        elif row.scenario_family != "LEGITIMATE" or patterns:
            errors.append(
                f"{row.logical_id} legitimate metadata contains a specialist label"
            )

    segment_counts = Counter(row.segment for row in nominations)
    for segment in range(SEGMENT_COUNT):
        if segment_counts[segment] != NOMINATIONS_PER_SEGMENT:
            errors.append(
                f"segment {segment} does not contain "
                f"{NOMINATIONS_PER_SEGMENT:,} nominations"
            )
        segment_rows = [row for row in nominations if row.segment == segment]
        segment_classes = Counter(row.training_disposition for row in segment_rows)
        fraud_per_segment = sum(FRAUD_PER_SEGMENT.values())
        legitimate_per_segment = NOMINATIONS_PER_SEGMENT - fraud_per_segment
        if segment_classes != Counter({
            "LEGITIMATE": legitimate_per_segment,
            "FRAUD": fraud_per_segment,
        }):
            errors.append(
                f"segment {segment} does not contain "
                f"{legitimate_per_segment:,}/{fraud_per_segment:,} labels"
            )
        expected_families = Counter(FRAUD_PER_SEGMENT)
        actual_families = Counter(
            row.scenario_family
            for row in segment_rows
            if row.training_disposition == "FRAUD"
        )
        if actual_families != expected_families:
            errors.append(
                f"segment {segment} fraud allocation is {dict(actual_families)}"
            )

        target_modes = Counter(
            row.context_mode
            for row in segment_rows
            if row.scenario_phase == "TARGET"
            and row.training_disposition == "FRAUD"
        )
        expected_modes = (
            Counter({"ACTIVE": 60})
            if segment == 0
            else Counter({"ACTIVE": 30, "ESTABLISHED": 30})
        )
        if target_modes != expected_modes:
            errors.append(
                f"segment {segment} context allocation is {dict(target_modes)}"
            )

        control_families = Counter(
            CONTROL_FAMILY_BY_VARIANT[row.scenario_variant]
            for row in segment_rows
            if row.scenario_phase == "TARGET"
            and row.training_disposition == "LEGITIMATE"
            and row.scenario_variant in CONTROL_FAMILY_BY_VARIANT
        )
        if control_families != Counter(FRAUD_PER_SEGMENT):
            errors.append(
                f"segment {segment} legitimate controls are "
                f"{dict(control_families)}"
            )

    scenario_rows: dict[str, list[SyntheticNomination]] = {}
    for row in nominations:
        if row.scenario_id:
            scenario_rows.setdefault(row.scenario_id, []).append(row)
        elif row.scenario_phase != "BACKGROUND" or row.context_mode != "NONE":
            errors.append(
                f"{row.logical_id} has scenario metadata without a scenario ID"
            )

    if len(scenario_rows) != fraud_count * 2:
        errors.append(
            f"expected {fraud_count * 2:,} fraud/control scenarios, "
            f"found {len(scenario_rows)}"
        )
    for scenario_id, rows in scenario_rows.items():
        targets = [row for row in rows if row.scenario_phase == "TARGET"]
        precursors = [row for row in rows if row.scenario_phase == "PRECURSOR"]
        if len(targets) != 1 or len(precursors) < 2:
            errors.append(
                f"{scenario_id} must contain at least two precursors and one target"
            )
            continue
        target = targets[0]
        behavior = target.scenario_family
        if target.training_disposition == "LEGITIMATE":
            behavior = CONTROL_FAMILY_BY_VARIANT.get(target.scenario_variant)
            if behavior is None:
                errors.append(f"{scenario_id} has an invalid control variant")
                continue
        if behavior not in SPECIALIST_FAMILIES:
            errors.append(f"{scenario_id} has unknown behavior {behavior}")
            continue
        target_time = datetime.fromisoformat(target.nomination_time_utc)
        precursor_times = [
            datetime.fromisoformat(row.nomination_time_utc) for row in precursors
        ]
        if any(row.training_disposition != "LEGITIMATE" for row in precursors):
            errors.append(f"{scenario_id} precursor is not labelled LEGITIMATE")
        if any(row.confirmed_patterns for row in precursors):
            errors.append(f"{scenario_id} precursor has a confirmed pattern")
        if any(value >= target_time for value in precursor_times):
            errors.append(f"{scenario_id} has a non-causal precursor timestamp")
        if any(
            row.context_mode != target.context_mode
            or row.scenario_variant != target.scenario_variant
            for row in precursors
        ):
            errors.append(f"{scenario_id} metadata is inconsistent")
        if target.context_mode == "ACTIVE":
            if any(row.segment != target.segment for row in precursors):
                errors.append(f"{scenario_id} active precursor is outside target segment")
            if max(target_time - value for value in precursor_times) > timedelta(days=2):
                errors.append(f"{scenario_id} active precursor is not recent")
        elif target.context_mode == "ESTABLISHED":
            latest_allowed_segment = max(0, target.segment - 2)
            established = [
                row for row in precursors
                if row.segment <= latest_allowed_segment
            ]
            active = [row for row in precursors if row.segment == target.segment]
            if target.segment == 0 or not established:
                errors.append(
                    f"{scenario_id} established precursor misses immutable history"
                )
            if behavior == "TEMPORAL_BURST" and len(active) < 3:
                errors.append(
                    f"{scenario_id} established burst lacks live causal events"
                )
            if behavior != "TEMPORAL_BURST" and active:
                errors.append(
                    f"{scenario_id} established scenario has active precursors"
                )
        else:
            errors.append(f"{scenario_id} has invalid context mode")

        precursor_edges = [
            (row.nominator_logical_id, row.beneficiary_logical_id)
            for row in precursors
        ]
        edges = set(precursor_edges)
        target_edge = (
            target.nominator_logical_id,
            target.beneficiary_logical_id,
        )
        if behavior == "RING":
            closes_three_user_ring = any(
                (target_edge[1], middle) in edges
                and (middle, target_edge[0]) in edges
                for middle in user_ids
            )
            closes_four_user_ring = any(
                (target_edge[1], first) in edges
                and (first, second) in edges
                and (second, target_edge[0]) in edges
                for first in user_ids
                for second in user_ids
            )
            if not (closes_three_user_ring or closes_four_user_ring):
                errors.append(f"{scenario_id} target does not close a ring")
        elif behavior == "RECIPROCAL":
            if (target_edge[1], target_edge[0]) not in edges:
                errors.append(f"{scenario_id} target lacks a prior reverse edge")
        elif behavior == "TEMPORAL_BURST":
            recent = [
                row for row in precursors
                if timedelta(0) < (
                    target_time - datetime.fromisoformat(row.nomination_time_utc)
                ) <= timedelta(hours=1)
                and row.beneficiary_logical_id == target_edge[1]
            ]
            if len(recent) < 3:
                errors.append(
                    f"{scenario_id} burst lacks three recent beneficiary events"
                )
        elif behavior == "SUPER_NOMINATOR":
            relevant = [
                edge for edge in precursor_edges if edge[0] == target_edge[0]
            ]
            if len(relevant) < 8 or len({edge[1] for edge in relevant}) < 8:
                errors.append(
                    f"{scenario_id} lacks super-nominator breadth"
                )
        elif behavior == "SUPER_BENEFICIARY":
            relevant = [
                edge for edge in precursor_edges if edge[1] == target_edge[1]
            ]
            if len(relevant) < 8 or len({edge[0] for edge in relevant}) < 8:
                errors.append(
                    f"{scenario_id} lacks super-beneficiary breadth"
                )
        elif behavior == "BIPARTITE_DENSE_BLOCK":
            all_edges = edges | {target_edge}
            left_candidates = {
                edge[0] for edge in all_edges if edge[1] == target_edge[1]
            }
            right_candidates = {
                edge[1] for edge in all_edges if edge[0] == target_edge[0]
            }
            completes_block = any(
                all(
                    (left, right) in all_edges
                    for left in (target_edge[0], left_a, left_b)
                    for right in (target_edge[1], right_a, right_b)
                )
                for left_a in left_candidates
                for left_b in left_candidates
                for right_a in right_candidates
                for right_b in right_candidates
                if left_a != left_b and right_a != right_b
            )
            if not completes_block:
                errors.append(f"{scenario_id} is not a complete 3x3 block")

    start = as_of - timedelta(days=365)
    dates = [datetime.fromisoformat(row.nomination_time_utc).date() for row in nominations]
    if dates and (min(dates) != start or max(dates) != as_of - timedelta(days=1)):
        errors.append("nomination dates do not span the complete 365-day window")
    daily_counts = Counter(dates)
    if len(daily_counts) != 365 or not (
        30 <= min(daily_counts.values()) <= max(daily_counts.values()) <= 50
    ):
        errors.append(
            "daily volume is missing dates or exceeds the 30..50 event bound"
        )

    participants = {
        logical_id
        for row in nominations
        for logical_id in (row.nominator_logical_id, row.beneficiary_logical_id)
    }
    if len(participants) != ACTIVE_USER_COUNT:
        errors.append(
            f"expected {ACTIVE_USER_COUNT} active graph participants, found {len(participants)}"
        )
    if any(row.category_name not in row.description for row in nominations):
        errors.append("one or more descriptions omit the exact category phrase")
    category_counts = Counter(row.category_name for row in nominations)
    expected_category_count = NOMINATION_COUNT // len(CATEGORIES)
    if category_counts != Counter(
        {category: expected_category_count for category in CATEGORIES}
    ):
        errors.append(f"category distribution is not balanced: {dict(category_counts)}")
    if any(
        not (
            CATEGORY_AMOUNT_BOUNDS[row.category_name][0]
            <= row.amount
            <= CATEGORY_AMOUNT_BOUNDS[row.category_name][1]
        )
        for row in nominations
    ):
        errors.append("one or more amounts fall outside their category limits")

    train = [row for row in nominations if row.segment in (1, 2, 3)]
    holdout = [row for row in nominations if row.segment == 4]
    confirmed_pattern_counts = Counter(
        pattern
        for row in nominations
        for pattern in row.confirmed_patterns
    )
    legitimate_control_counts = Counter(
        CONTROL_FAMILY_BY_VARIANT[row.scenario_variant]
        for row in nominations
        if row.scenario_phase == "TARGET"
        and row.training_disposition == "LEGITIMATE"
        and row.scenario_variant in CONTROL_FAMILY_BY_VARIANT
    )
    expected_specialist_counts = Counter({family: 50 for family in SPECIALIST_FAMILIES})
    if confirmed_pattern_counts != expected_specialist_counts:
        errors.append(
            f"confirmed pattern counts are {dict(confirmed_pattern_counts)}"
        )
    if legitimate_control_counts != expected_specialist_counts:
        errors.append(
            f"legitimate control counts are {dict(legitimate_control_counts)}"
        )
    fold_label_evidence = {
        family: {
            "training_positive_count": sum(
                family in row.confirmed_patterns
                for row in train
            ),
            "selection_fold_positive_counts": [
                sum(
                    family in row.confirmed_patterns
                    for row in nominations
                    if row.segment == segment
                )
                for segment in (2, 3)
            ],
            "final_holdout_positive_count": sum(
                family in row.confirmed_patterns for row in holdout
            ),
        }
        for family in SPECIALIST_FAMILIES
    }
    expected_evidence = {
        "training_positive_count": 30,
        "selection_fold_positive_counts": [10, 10],
        "final_holdout_positive_count": 10,
    }
    if any(
        evidence != expected_evidence
        for evidence in fold_label_evidence.values()
    ):
        errors.append(
            f"specialist fold evidence is {fold_label_evidence}"
        )

    if errors:
        raise ValueError("Synthetic corpus validation failed:\n- " + "\n- ".join(errors))

    return {
        "valid": True,
        "pattern_taxonomy_version": PATTERN_TAXONOMY_VERSION,
        "corpus_user_count": len(users),
        "operational_admin_count": 1,
        "total_directory_and_sql_users": len(users) + 1,
        "active_graph_participants": len(participants),
        "nomination_count": len(nominations),
        "legitimate_count": class_counts["LEGITIMATE"],
        "fraud_count": class_counts["FRAUD"],
        "rolling_train_legitimate_count": sum(
            row.training_disposition == "LEGITIMATE" for row in train
        ),
        "rolling_train_fraud_count": sum(
            row.training_disposition == "FRAUD" for row in train
        ),
        "holdout_legitimate_count": sum(
            row.training_disposition == "LEGITIMATE" for row in holdout
        ),
        "holdout_fraud_count": sum(
            row.training_disposition == "FRAUD" for row in holdout
        ),
        "causal_scenario_count": len(scenario_rows),
        "causal_precursor_count": sum(
            row.scenario_phase == "PRECURSOR" for row in nominations
        ),
        "active_context_target_count": sum(
            row.scenario_phase == "TARGET"
            and row.context_mode == "ACTIVE"
            and row.training_disposition == "FRAUD"
            for row in nominations
        ),
        "established_context_target_count": sum(
            row.scenario_phase == "TARGET"
            and row.context_mode == "ESTABLISHED"
            and row.training_disposition == "FRAUD"
            for row in nominations
        ),
        "fraud_scenarios": dict(sorted(Counter(
            row.scenario_family
            for row in nominations
            if row.training_disposition == "FRAUD"
        ).items())),
        "confirmed_pattern_counts": dict(sorted(confirmed_pattern_counts.items())),
        "legitimate_control_counts": dict(sorted(legitimate_control_counts.items())),
        "fold_label_evidence": fold_label_evidence,
        "category_counts": dict(sorted(category_counts.items())),
        "window_start": start.isoformat(),
        "window_end": (as_of - timedelta(days=1)).isoformat(),
    }
