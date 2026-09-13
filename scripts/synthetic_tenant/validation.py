"""Fail-closed validation for the generated Synthetics Inc. logical corpus."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from .scenarios import (
    ACTIVE_USER_COUNT,
    CATEGORIES,
    CATEGORY_AMOUNT_BOUNDS,
    CORPUS_USER_COUNT,
    DEPARTMENTS,
    FRAUD_PER_SEGMENT,
    NOMINATION_COUNT,
    NOMINATIONS_PER_SEGMENT,
    SEGMENT_COUNT,
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

    class_counts = Counter(row.training_disposition for row in nominations)
    if class_counts != Counter({"LEGITIMATE": 4_900, "FRAUD": 100}):
        errors.append(f"expected 4,900/100 class balance, found {dict(class_counts)}")

    segment_counts = Counter(row.segment for row in nominations)
    for segment in range(SEGMENT_COUNT):
        if segment_counts[segment] != NOMINATIONS_PER_SEGMENT:
            errors.append(f"segment {segment} does not contain 1,000 nominations")
        segment_rows = [row for row in nominations if row.segment == segment]
        segment_classes = Counter(row.training_disposition for row in segment_rows)
        if segment_classes != Counter({"LEGITIMATE": 980, "FRAUD": 20}):
            errors.append(f"segment {segment} does not contain 980/20 labels")
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

        fraud_rows = {
            family: [row for row in segment_rows if row.scenario_family == family]
            for family in FRAUD_PER_SEGMENT
        }
        ring_edges = {
            (row.nominator_logical_id, row.beneficiary_logical_id)
            for row in fraud_rows["RING"]
        }
        closed_ring_edges = sum(
            any((beneficiary, third) in ring_edges and (third, nominator) in ring_edges
                for third in user_ids)
            for nominator, beneficiary in ring_edges
        )
        if closed_ring_edges != 6:
            errors.append(f"segment {segment} ring rows do not form two closed cycles")

        reciprocal_edges = {
            (row.nominator_logical_id, row.beneficiary_logical_id)
            for row in fraud_rows["RECIPROCAL"]
        }
        if not reciprocal_edges or any(
            (beneficiary, nominator) not in reciprocal_edges
            for nominator, beneficiary in reciprocal_edges
        ):
            errors.append(f"segment {segment} reciprocal rows lack reverse edges")

        concentration = Counter(
            row.nominator_logical_id for row in fraud_rows["CONCENTRATION"]
        )
        if max(concentration.values(), default=0) != 4:
            errors.append(f"segment {segment} concentration rows lack a serial nominator")

        burst_times = [
            datetime.fromisoformat(row.nomination_time_utc)
            for row in fraud_rows["BURST"]
        ]
        if burst_times and max(burst_times) - min(burst_times) > timedelta(minutes=5):
            errors.append(f"segment {segment} burst rows are not temporally coordinated")

    start = as_of - timedelta(days=365)
    dates = [datetime.fromisoformat(row.nomination_time_utc).date() for row in nominations]
    if dates and (min(dates) != start or max(dates) != as_of - timedelta(days=1)):
        errors.append("nomination dates do not span the complete 365-day window")
    daily_counts = Counter(dates)
    if Counter(daily_counts.values()) != Counter({14: 255, 13: 110}):
        errors.append("daily volume is not exactly 255x14 plus 110x13")

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
    if category_counts != Counter({category: 1_000 for category in CATEGORIES}):
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

    if errors:
        raise ValueError("Synthetic corpus validation failed:\n- " + "\n- ".join(errors))

    train = [row for row in nominations if row.segment in (1, 2, 3)]
    holdout = [row for row in nominations if row.segment == 4]
    return {
        "valid": True,
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
        "fraud_scenarios": dict(sorted(Counter(
            row.scenario_family
            for row in nominations
            if row.training_disposition == "FRAUD"
        ).items())),
        "category_counts": dict(sorted(category_counts.items())),
        "window_start": start.isoformat(),
        "window_end": (as_of - timedelta(days=1)).isoformat(),
    }
