"""Pure, deterministic generators for the Synthetics Inc. corpus.

This module performs no database, Microsoft Graph, Service Bus, or LLM calls.
The generated logical records are validated before any future provisioning
adapter is allowed to persist them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import uuid


GENERATOR_VERSION = "synthetics-inc-v1.1"
GENERATOR_NAMESPACE = uuid.UUID("bbf46d6d-a7c0-4f45-8ba0-2cce41121085")
UPN_DOMAIN = "synthetics.terian-services.com"
CORPUS_USER_COUNT = 400
ACTIVE_USER_COUNT = 360
NOMINATION_COUNT = 5_000
SEGMENT_COUNT = 5
NOMINATIONS_PER_SEGMENT = 1_000

DEPARTMENTS = (
    "Sales",
    "Finance",
    "Information Technology",
    "Cloud Computing",
    "Legal",
    "Human Resources",
    "Marketing",
    "Customer Success",
    "Operations",
    "Product Management",
    "Engineering",
    "Data & Analytics",
    "Security",
    "Compliance & Risk",
    "Procurement",
    "Research & Development",
    "Professional Services",
    "Executive Leadership",
)

CATEGORIES = (
    "Innovation & Problem Solving",
    "Teamwork & Collaboration",
    "Leadership & Mentorship",
    "Customer Excellence",
    "Going Above & Beyond",
)

# Snapshot of the Tenant 1 category policy cloned for this corpus. Keeping the
# bounds with the deterministic generator makes dry runs reproducible; the SQL
# adapter independently checks every generated amount against the freshly
# cloned database policy before it inserts anything.
CATEGORY_AMOUNT_BOUNDS = {
    "Innovation & Problem Solving": (500, 5_000),
    "Teamwork & Collaboration": (1_000, 5_000),
    "Leadership & Mentorship": (1_000, 6_000),
    "Customer Excellence": (50, 3_000),
    "Going Above & Beyond": (200, 3_500),
}

FRAUD_PER_SEGMENT = {
    "RING": 6,
    "RECIPROCAL": 4,
    "CONCENTRATION": 4,
    "BURST": 3,
    "AMOUNT": 2,
    "MIXED": 1,
}

HARD_NEGATIVE_VARIANTS = (
    "OPEN_CHAIN",
    "DISTANT_RECIPROCAL",
    "CULTURE_CHAMPION",
    "MENTOR_CONCENTRATION",
    "PROJECT_LAUNCH_BURST",
    "JUSTIFIED_MAXIMUM_AMOUNT",
    "CLOSE_COLLABORATORS",
)


@dataclass(frozen=True)
class SyntheticUser:
    logical_id: str
    stable_id: str
    first_name: str
    last_name: str
    display_name: str
    upn: str
    department: str
    manager_logical_id: str | None
    account_enabled: bool = False


@dataclass(frozen=True)
class SyntheticNomination:
    logical_id: str
    stable_id: str
    segment: int
    nomination_time_utc: str
    nominator_logical_id: str
    beneficiary_logical_id: str
    approver_logical_id: str
    category_key: str
    category_name: str
    amount: int
    description: str
    status: str
    training_disposition: str
    scenario_family: str
    scenario_variant: str


def _load_names() -> tuple[list[str], list[str]]:
    path = Path(__file__).with_name("armenian_names.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["first_names"], data["last_names"]


def _upn_part(value: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", value.lower())


def generate_users(seed: int) -> list[SyntheticUser]:
    """Generate the stable 400-person Armenian corpus and manager hierarchy."""
    first_names, last_names = _load_names()
    combinations = [(first, last) for first in first_names for last in last_names]
    rng = random.Random(seed)
    rng.shuffle(combinations)
    selected = combinations[:CORPUS_USER_COUNT]

    logical_ids = [f"SYN-U{index:04d}" for index in range(1, 401)]
    # One executive, eight division leads, and 45 people managers. Remaining
    # staff report to a manager in their own department.
    department_managers: dict[str, list[str]] = {dept: [] for dept in DEPARTMENTS}
    for index in range(9, 54):
        department = DEPARTMENTS[(index - 9) % len(DEPARTMENTS)]
        department_managers[department].append(logical_ids[index])

    users: list[SyntheticUser] = []
    for index, ((first, last), logical_id) in enumerate(zip(selected, logical_ids)):
        if index == 0:
            department = "Executive Leadership"
            manager = None
        elif index < 9:
            department = DEPARTMENTS[(index - 1) * 2 % (len(DEPARTMENTS) - 1)]
            manager = logical_ids[0]
        elif index < 54:
            department = DEPARTMENTS[(index - 9) % len(DEPARTMENTS)]
            manager = logical_ids[1 + (DEPARTMENTS.index(department) % 8)]
        else:
            department = DEPARTMENTS[(index - 54) % len(DEPARTMENTS)]
            managers = department_managers[department]
            manager = managers[(index - 54) // len(DEPARTMENTS) % len(managers)]

        upn = f"{_upn_part(first)}.{_upn_part(last)}@{UPN_DOMAIN}"
        users.append(SyntheticUser(
            logical_id=logical_id,
            stable_id=str(uuid.uuid5(GENERATOR_NAMESPACE, f"user:{index + 1}")),
            first_name=first,
            last_name=last,
            display_name=f"{first} {last}",
            upn=upn,
            department=department,
            manager_logical_id=manager,
        ))
    return users


def _description(category: str, variant: int) -> str:
    subjects = (
        "led a cross-functional delivery through an unexpected deadline",
        "redesigned a recurring workflow and removed avoidable handoffs",
        "helped a customer team resolve a complex implementation issue",
        "coached colleagues while completing a demanding project milestone",
        "identified a service risk and coordinated a practical response",
    )
    impacts = (
        "The work reduced delays, improved clarity, and gave the team a repeatable approach.",
        "The result improved service quality and helped colleagues meet their commitments.",
        "Their initiative produced a measurable outcome and strengthened collaboration.",
        "This contribution removed a persistent obstacle and improved the customer experience.",
        "The approach delivered a timely result while sharing knowledge across the team.",
    )
    return (
        f"For {category}, the nominee {subjects[variant % len(subjects)]}. "
        f"{impacts[(variant * 3) % len(impacts)]} The award recognizes a specific, "
        "well-documented contribution and its positive business impact."
    )


def _fraud_parties(
    active: list[SyntheticUser], segment: int, ordinal: int, family: str
) -> tuple[SyntheticUser, SyntheticUser]:
    # Segment-specific cohorts reduce actor memorization between train and holdout.
    cohort = segment * 70
    if family == "RING":
        cycle_base = cohort + (ordinal // 3) * 3
        ring = [active[(cycle_base + offset) % len(active)] for offset in range(3)]
        step = ordinal % 3
        return ring[step], ring[(step + 1) % 3]
    if family == "RECIPROCAL":
        pair_base = cohort + 10 + (ordinal // 2) * 2
        pair_a = active[pair_base % len(active)]
        pair_b = active[(pair_base + 1) % len(active)]
        return (pair_a, pair_b) if ordinal % 2 == 0 else (pair_b, pair_a)
    if family == "CONCENTRATION":
        return active[(cohort + 20) % len(active)], active[(cohort + 21 + ordinal % 2) % len(active)]
    if family == "BURST":
        return active[(cohort + 30 + ordinal) % len(active)], active[(cohort + 34) % len(active)]
    if family == "MIXED":
        return active[(cohort + 40) % len(active)], active[(cohort + 41) % len(active)]
    return active[(cohort + 50 + ordinal) % len(active)], active[(cohort + 55) % len(active)]


def _legitimate_parties(
    active: list[SyntheticUser], global_index: int, variant: str
) -> tuple[SyntheticUser, SyntheticUser]:
    """Create plausible hard negatives without making their label obvious."""
    generic_nominator = active[global_index % len(active)]
    generic_beneficiary = active[(global_index * 7 + 37) % len(active)]
    if variant == "CULTURE_CHAMPION":
        nominator = active[(global_index // 7) % 12]
        beneficiary = generic_beneficiary
    elif variant == "MENTOR_CONCENTRATION":
        mentor_base = 120 + ((global_index // 35) % 18)
        nominator = active[mentor_base % len(active)]
        beneficiary = active[(mentor_base + 1 + global_index % 6) % len(active)]
    elif variant == "CLOSE_COLLABORATORS":
        team_base = (global_index // 42 * 6) % len(active)
        nominator = active[(team_base + global_index % 6) % len(active)]
        beneficiary = active[(team_base + (global_index + 1) % 6) % len(active)]
    else:
        nominator = generic_nominator
        beneficiary = generic_beneficiary
    if beneficiary.logical_id == nominator.logical_id:
        beneficiary = active[(active.index(beneficiary) + 1) % len(active)]
    return nominator, beneficiary


def generate_nominations(
    users: list[SyntheticUser], seed: int, as_of: date
) -> list[SyntheticNomination]:
    """Generate exactly 5,000 nominations in five balanced temporal segments."""
    rng = random.Random(seed ^ 0x5A17)
    active = users[:ACTIVE_USER_COUNT]
    by_id = {user.logical_id: user for user in users}
    start = as_of - timedelta(days=365)
    nominations: list[SyntheticNomination] = []

    for segment in range(SEGMENT_COUNT):
        burst_positions = [500, 501, 502]
        available_positions = [
            value
            for value in range(NOMINATIONS_PER_SEGMENT)
            if value not in burst_positions
        ]
        other_positions = rng.sample(available_positions, 17)
        assignments = [
            (family, family_ordinal)
            for family, count in FRAUD_PER_SEGMENT.items()
            if family != "BURST"
            for family_ordinal in range(count)
        ]
        rng.shuffle(assignments)
        fraud_by_position = dict(zip(other_positions, assignments))
        fraud_by_position.update({
            position: ("BURST", family_ordinal)
            for family_ordinal, position in enumerate(burst_positions)
        })

        for ordinal in range(NOMINATIONS_PER_SEGMENT):
            global_index = segment * NOMINATIONS_PER_SEGMENT + ordinal
            assignment = fraud_by_position.get(ordinal)
            family = None
            if assignment:
                family, family_ordinal = assignment
                nominator, beneficiary = _fraud_parties(
                    active, segment, family_ordinal, family
                )
                disposition = "FRAUD"
                variant = family
            else:
                disposition = "LEGITIMATE"
                variant = HARD_NEGATIVE_VARIANTS[global_index % len(HARD_NEGATIVE_VARIANTS)]
                nominator, beneficiary = _legitimate_parties(
                    active, global_index, variant
                )

            # Three is coprime to the five category count, yielding an even
            # category mix inside every temporal segment instead of coupling a
            # fold to one category.
            category_index = (global_index * 3 + segment) % len(CATEGORIES)
            category = CATEGORIES[category_index]
            day_in_segment = ordinal * 73 // NOMINATIONS_PER_SEGMENT
            nomination_date = start + timedelta(days=segment * 73 + day_in_segment)
            minute = 8 * 60 + ((global_index * 37) % (11 * 60))
            if family == "BURST" or variant == "PROJECT_LAUNCH_BURST":
                minute = 10 * 60 + (ordinal % 6)
            timestamp = datetime.combine(
                nomination_date,
                time(hour=minute // 60, minute=minute % 60),
                tzinfo=timezone.utc,
            )
            minimum_amount, maximum_amount = CATEGORY_AMOUNT_BOUNDS[category]
            if family in {"AMOUNT", "MIXED"}:
                amount = maximum_amount
            elif variant == "JUSTIFIED_MAXIMUM_AMOUNT":
                amount = maximum_amount
            else:
                amount_steps = (maximum_amount - minimum_amount) // 50
                amount = minimum_amount + 50 * (
                    (global_index * 137) % (amount_steps + 1)
                )

            approver_id = nominator.manager_logical_id or users[1].logical_id
            status = (
                ("Rejected" if global_index % 2 == 0 else "Paid")
                if disposition == "FRAUD"
                else ("Pending" if segment == 4 and ordinal % 25 == 0
                      else ("Approved" if global_index % 3 == 0 else "Paid"))
            )
            logical_id = f"SYN-N{global_index + 1:05d}"
            nominations.append(SyntheticNomination(
                logical_id=logical_id,
                stable_id=str(uuid.uuid5(GENERATOR_NAMESPACE, f"nomination:{global_index + 1}")),
                segment=segment,
                nomination_time_utc=timestamp.isoformat(),
                nominator_logical_id=nominator.logical_id,
                beneficiary_logical_id=beneficiary.logical_id,
                approver_logical_id=by_id[approver_id].logical_id,
                category_key=f"CATEGORY_{category_index + 1:02d}",
                category_name=category,
                amount=amount,
                description=_description(category, global_index),
                status=status,
                training_disposition=disposition,
                scenario_family=family or "LEGITIMATE",
                scenario_variant=variant,
            ))
    return nominations


def corpus_hash(users: list[SyntheticUser], nominations: list[SyntheticNomination]) -> str:
    payload = {
        "users": [asdict(user) for user in users],
        "nominations": [asdict(nomination) for nomination in nominations],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
