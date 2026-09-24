"""Pure, deterministic generators for the Synthetics Inc. corpus.

This module performs no database, Microsoft Graph, Service Bus, or LLM calls.
The generated logical records are validated before any future provisioning
adapter is allowed to persist them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import uuid

from .descriptions import description_for_nomination


GENERATOR_VERSION = "synthetics-inc-v5.0"
PATTERN_TAXONOMY_VERSION = "gnn-v3-patterns-v1"
DIRECTORY_SEED = 20260912
GENERATOR_NAMESPACE = uuid.UUID("bbf46d6d-a7c0-4f45-8ba0-2cce41121085")
UPN_DOMAIN = "synthetics.terian-services.com"
CORPUS_USER_COUNT = 400
ACTIVE_USER_COUNT = 360
QUIET_TEAM_MANAGER_IDS = (
    "SYN-U0037", "SYN-U0038", "SYN-U0039", "SYN-U0040",
)
LOW_RECOGNITION_COHORT_SIZE = 12
NOMINATION_COUNT = 15_000
SEGMENT_COUNT = 5
NOMINATIONS_PER_SEGMENT = 3_000

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

SPECIALIST_FAMILIES = (
    "RING",
    "RECIPROCAL",
    "TEMPORAL_BURST",
    "SUPER_NOMINATOR",
    "SUPER_BENEFICIARY",
    "BIPARTITE_DENSE_BLOCK",
)
FRAUD_PER_SEGMENT = {family: 10 for family in SPECIALIST_FAMILIES}
ACTIVE_CONTEXT_FAMILIES = {family: 5 for family in SPECIALIST_FAMILIES}
ESTABLISHED_CONTEXT_FAMILIES = {family: 5 for family in SPECIALIST_FAMILIES}

# Fraud and feature-matched legitimate control targets occupy disjoint ranges.
# The sixteen-position stride leaves room for the largest active precursor set.
CONTROL_TARGET_POSITIONS = tuple(1_100 + index * 14 for index in range(60))
FRAUD_TARGET_POSITIONS = tuple(2_000 + index * 16 for index in range(60))

BACKGROUND_VARIANTS = (
    "ROUTINE_COLLABORATION",
    "PROJECT_MILESTONE",
    "CUSTOMER_DELIVERY",
    "MENTORSHIP_RECOGNITION",
    "SERVICE_IMPROVEMENT",
    "CROSS_FUNCTIONAL_DELIVERY",
)

CONTROL_VARIANTS = {
    "RING": "OPEN_COLLABORATION_CHAIN",
    "RECIPROCAL": "ORDINARY_MUTUAL_RECOGNITION",
    "TEMPORAL_BURST": "PROJECT_DEADLINE_ACTIVITY",
    "SUPER_NOMINATOR": "CULTURE_CHAMPION_OUTREACH",
    "SUPER_BENEFICIARY": "POPULAR_PROJECT_LEAD",
    "BIPARTITE_DENSE_BLOCK": "CROSS_FUNCTIONAL_RECOGNITION_CAMPAIGN",
}
CONTROL_FAMILY_BY_VARIANT = {
    variant: family for family, variant in CONTROL_VARIANTS.items()
}


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
    scenario_id: str | None
    scenario_phase: str
    context_mode: str
    confirmed_patterns: tuple[str, ...]


@dataclass(frozen=True)
class _ScenarioEvent:
    scenario_id: str
    family: str
    disposition: str
    scenario_variant: str
    family_ordinal: int
    context_mode: str
    phase: str
    phase_ordinal: int
    timing_mode: str
    timing_ordinal: int
    target_segment: int
    target_position: int
    nominator_logical_id: str
    beneficiary_logical_id: str


def _target_specs(segment: int) -> list[tuple[str, int, str]]:
    """Return the exact 60 target families and context modes for a segment."""
    specs: list[tuple[str, int, str]] = []
    family_ordinals: dict[str, int] = {family: 0 for family in FRAUD_PER_SEGMENT}
    for context_mode, allocation in (
        ("ACTIVE", ACTIVE_CONTEXT_FAMILIES),
        ("ESTABLISHED", ESTABLISHED_CONTEXT_FAMILIES),
    ):
        for family, count in allocation.items():
            for _ in range(count):
                specs.append((family, family_ordinals[family], context_mode))
                family_ordinals[family] += 1

    if segment == 0:
        # S0 is graph-history warm-up rather than a supervised rolling target.
        # Its target rows still have causal precursors, but no earlier segment
        # exists from which an established weekly context could be drawn.
        specs = [(family, ordinal, "ACTIVE") for family, ordinal, _ in specs]
    return specs


def _scenario_parties(
    active: list[SyntheticUser],
    segment: int,
    target_index: int,
    family: str,
    context_mode: str,
    *,
    control: bool,
) -> tuple[
    list[tuple[str, SyntheticUser, SyntheticUser]],
    tuple[SyntheticUser, SyntheticUser],
]:
    """Return causal precursor edges and the target edge for one v4 scenario."""

    pool_size = len(active) // 2
    pool_start = pool_size if control else 0
    base = (segment * 53 + target_index * 13) % pool_size

    def user(offset: int) -> SyntheticUser:
        return active[pool_start + ((base + offset) % pool_size)]

    a, b, c, d, e, f, g, h, i, j, k, user_l, m, n = (
        user(offset) for offset in range(14)
    )
    timing = "ACTIVE" if context_mode == "ACTIVE" else "ESTABLISHED"

    def complete(
        precursors: list[tuple[str, SyntheticUser, SyntheticUser]],
        target: tuple[SyntheticUser, SyntheticUser],
        benign_context: tuple[
            tuple[SyntheticUser, SyntheticUser], ...
        ],
    ) -> tuple[
        list[tuple[str, SyntheticUser, SyntheticUser]],
        tuple[SyntheticUser, SyntheticUser],
    ]:
        if control:
            precursors.extend(
                (timing, source, destination)
                for source, destination in benign_context
            )
        return precursors, target

    if family == "RING":
        if target_index % 2:
            return complete([
                (timing, a, b),
                (timing, b, c),
                (timing, c, d),
            ], (d, a), ((b, k), (k, user_l), (user_l, m), (m, n)))
        return complete(
            [(timing, a, b), (timing, b, c)],
            (c, a),
            ((b, k), (k, user_l), (user_l, m), (m, n)),
        )
    if family == "RECIPROCAL":
        return complete(
            [(timing, a, b), (timing, a, c)],
            (b, a),
            ((c, d), (d, e), (e, f), (f, g)),
        )
    if family == "TEMPORAL_BURST":
        active_burst = [
            ("ACTIVE", d, f),
            ("ACTIVE", e, f),
            ("ACTIVE", g, f),
            ("ACTIVE", h, f),
        ]
        established_context = (
            [("ESTABLISHED", a, b), ("ESTABLISHED", b, c)]
            if context_mode == "ESTABLISHED" else []
        )
        return complete(
            [*established_context, *active_burst],
            (i, f),
            ((d, a), (e, b), (g, c), (h, j)),
        )
    if family == "SUPER_NOMINATOR":
        return complete(
            [
                (timing, a, beneficiary)
                for beneficiary in (b, c, d, e, f, g, h, i)
            ],
            (a, j),
            ((b, k), (c, user_l), (d, m), (e, n)),
        )
    if family == "SUPER_BENEFICIARY":
        return complete(
            [
                (timing, nominator, j)
                for nominator in (a, b, c, d, e, f, g, h)
            ],
            (i, j),
            ((k, a), (user_l, b), (m, c), (n, d)),
        )
    if family == "BIPARTITE_DENSE_BLOCK":
        left = (a, b, c)
        right = (d, e, f)
        precursors = [
            (timing, nominator, beneficiary)
            for nominator in left for beneficiary in right
            if not (nominator is c and beneficiary is f)
        ]
        return complete(
            precursors,
            (c, f),
            ((d, g), (e, h), (a, i), (b, j)),
        )
    raise ValueError(f"Unsupported v4 specialist family: {family}")


def _scenario_plan(active: list[SyntheticUser]) -> dict[tuple[int, int], _ScenarioEvent]:
    """Build a collision-free schedule of causal precursors and fraud targets."""
    plan: dict[tuple[int, int], _ScenarioEvent] = {}
    established_next_position = [40 for _ in range(SEGMENT_COUNT)]

    for segment in range(SEGMENT_COUNT):
        for disposition, control, positions in (
            ("LEGITIMATE", True, CONTROL_TARGET_POSITIONS),
            ("FRAUD", False, FRAUD_TARGET_POSITIONS),
        ):
            for target_index, (
                (family, family_ordinal, context_mode), target_position
            ) in enumerate(zip(_target_specs(segment), positions)):
                scenario_variant = (
                    CONTROL_VARIANTS[family]
                    if control else f"{context_mode}_{family}_V1"
                )
                scenario_id = (
                    f"S{segment}-{context_mode}-{disposition}-"
                    f"{'CONTROL' if control else family}-"
                    f"{target_index + 1:02d}"
                )
                precursor_edges, target_edge = _scenario_parties(
                    active,
                    segment,
                    target_index,
                    family,
                    context_mode,
                    control=control,
                )
                active_edges = [
                    edge for edge in precursor_edges if edge[0] == "ACTIVE"
                ]
                active_positions = list(range(
                    target_position - len(active_edges), target_position
                ))
                active_position_index = 0

                for phase_ordinal, (timing_mode, nominator, beneficiary) in enumerate(
                    precursor_edges, start=1
                ):
                    if timing_mode == "ACTIVE":
                        event_segment = segment
                        position = active_positions[active_position_index]
                        active_position_index += 1
                        timing_ordinal = active_position_index
                    else:
                        event_segment = max(0, segment - 2)
                        position = established_next_position[event_segment]
                        established_next_position[event_segment] += 1
                        timing_ordinal = 0
                    key = (event_segment, position)
                    if key in plan:
                        raise ValueError(
                            f"Causal scenario schedule collision at {key}"
                        )
                    plan[key] = _ScenarioEvent(
                        scenario_id=scenario_id,
                        family=family,
                        disposition="LEGITIMATE",
                        scenario_variant=scenario_variant,
                        family_ordinal=family_ordinal,
                        context_mode=context_mode,
                        phase="PRECURSOR",
                        phase_ordinal=phase_ordinal,
                        timing_mode=timing_mode,
                        timing_ordinal=timing_ordinal,
                        target_segment=segment,
                        target_position=target_position,
                        nominator_logical_id=nominator.logical_id,
                        beneficiary_logical_id=beneficiary.logical_id,
                    )

                key = (segment, target_position)
                if key in plan:
                    raise ValueError(f"Causal scenario target collision at {key}")
                plan[key] = _ScenarioEvent(
                    scenario_id=scenario_id,
                    family=family,
                    disposition=disposition,
                    scenario_variant=scenario_variant,
                    family_ordinal=family_ordinal,
                    context_mode=context_mode,
                    phase="TARGET",
                    phase_ordinal=len(precursor_edges) + 1,
                    timing_mode="ACTIVE",
                    timing_ordinal=len(active_edges) + 1,
                    target_segment=segment,
                    target_position=target_position,
                    nominator_logical_id=target_edge[0].logical_id,
                    beneficiary_logical_id=target_edge[1].logical_id,
                )
    return plan


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


def participation_cohorts(
    users: list[SyntheticUser],
) -> tuple[set[str], tuple[str, ...]]:
    """Return four complete quiet teams and twelve non-scenario nominators."""
    by_id = {user.logical_id: user for user in users}
    quiet = set(QUIET_TEAM_MANAGER_IDS)
    for manager_id in QUIET_TEAM_MANAGER_IDS:
        if manager_id not in by_id:
            raise ValueError(f"Missing quiet-team manager {manager_id}")
        reports = [
            user for user in users if user.manager_logical_id == manager_id
        ]
        if len(reports) != 9:
            raise ValueError(f"Quiet team {manager_id} has {len(reports)} reports")
        quiet.update(user.logical_id for user in reports)
    cohort = tuple(
        user.logical_id for user in users
        if user.manager_logical_id is not None
        and user.logical_id not in quiet
    )[-LOW_RECOGNITION_COHORT_SIZE:]
    if len(quiet) != 40 or len(cohort) != LOW_RECOGNITION_COHORT_SIZE:
        raise ValueError("Invalid v5 participation cohorts")
    if len({by_id[user_id].department for user_id in cohort}) < 6:
        raise ValueError("Low-recognition cohort lacks department diversity")
    return quiet, cohort


def _legitimate_parties(
    active: list[SyntheticUser], beneficiaries: list[SyntheticUser],
    cohort: tuple[str, ...], global_index: int, background_ordinal: int, seed: int,
) -> tuple[SyntheticUser, SyntheticUser]:
    """Seed-stable, department-weighted background pairing without pair cycles."""
    rng = random.Random(f"v5:pair:{seed}:{global_index}")
    # Guarantee every eligible user participates, including the root, and give
    # the designated low-recognition cohort at least eight outgoing edges.
    if background_ordinal < len(active):
        nominator = active[background_ordinal]
    elif background_ordinal < len(active) + len(cohort) * 8:
        cohort_id = cohort[(background_ordinal - len(active)) % len(cohort)]
        nominator = next(user for user in active if user.logical_id == cohort_id)
    else:
        nominator = rng.choice(active)
    candidates = [
        user for user in beneficiaries if user.logical_id != nominator.logical_id
    ]
    same_department = [
        user for user in candidates if user.department == nominator.department
    ]
    cross_department = [
        user for user in candidates if user.department != nominator.department
    ]
    pool = (
        same_department if rng.random() < 2 / 3 and same_department
        else cross_department if cross_department else candidates
    )
    beneficiary = rng.choice(pool)
    return nominator, beneficiary


def _apply_intentional_description_reuse(
    nominations: list[SyntheticNomination],
) -> list[SyntheticNomination]:
    """Create exact-reuse trios whose text, beneficiary, and amount agree.

    Three trios per category include one fraud target plus two legitimate
    background nominations for the *same* beneficiary. The remaining 47 trios
    per category are ordinary recognition campaigns. Specialist topology is
    untouched; only background recipients/amounts can be harmonized.
    """
    result = list(nominations)
    background: dict[tuple[str, int], list[int]] = defaultdict(list)
    fraud_targets: dict[tuple[str, int], list[int]] = defaultdict(list)
    for index, row in enumerate(result):
        key = (row.category_name, row.segment)
        if row.scenario_phase == "BACKGROUND":
            background[key].append(index)
        elif row.scenario_phase == "TARGET" and row.training_disposition == "FRAUD":
            fraud_targets[key].append(index)

    used: set[int] = set()
    for category in CATEGORIES:
        for segment in (0, 2, 4):
            for anchor_index in fraud_targets[(category, segment)]:
                anchor = result[anchor_index]
                matches = [
                    index for index in background[(category, segment)]
                    if index not in used
                    and result[index].beneficiary_logical_id
                    == anchor.beneficiary_logical_id
                ]
                if len(matches) < 2:
                    continue
                for index in matches[:2]:
                    result[index] = replace(
                        result[index], amount=anchor.amount,
                        description=anchor.description,
                    )
                    used.add(index)
                break
            else:
                raise ValueError(
                    f"No fraud-linked description trio for {category} segment {segment}"
                )

        # 10, 10, 10, 10, 7 benign trios across the five time segments.
        for segment, group_count in enumerate((10, 10, 10, 10, 7)):
            candidates = [
                index for index in background[(category, segment)]
                if index not in used
            ]
            stride = len(candidates) // group_count
            for group_number in range(group_count):
                block = candidates[
                    group_number * stride:(group_number + 1) * stride
                ]
                block = [index for index in block if index not in used]
                anchor_index = block[0]
                anchor = result[anchor_index]
                partners: list[int] = []
                nominators = {anchor.nominator_logical_id}
                for index in block[1:]:
                    row = result[index]
                    if (
                        row.nominator_logical_id == anchor.beneficiary_logical_id
                        or row.nominator_logical_id in nominators
                    ):
                        continue
                    partners.append(index)
                    nominators.add(row.nominator_logical_id)
                    if len(partners) == 2:
                        break
                if len(partners) != 2:
                    raise ValueError(
                        f"No benign description trio for {category} segment {segment}"
                    )
                used.add(anchor_index)
                for index in partners:
                    result[index] = replace(
                        result[index],
                        beneficiary_logical_id=anchor.beneficiary_logical_id,
                        approver_logical_id=anchor.approver_logical_id,
                        amount=anchor.amount,
                        description=anchor.description,
                    )
                    used.add(index)
    return result


def generate_nominations(
    users: list[SyntheticUser], seed: int, as_of: date
) -> list[SyntheticNomination]:
    """Generate the v5 corpus with direct specialist labels and controls."""
    quiet, cohort = participation_cohorts(users)
    active = [user for user in users if user.logical_id not in quiet]
    if len(active) != ACTIVE_USER_COUNT:
        raise ValueError("The active corpus needs 360 users")
    scenario_active = [
        user for user in active
        if user.manager_logical_id is not None and user.logical_id not in cohort
    ]
    background_beneficiaries = [
        user for user in active
        if user.manager_logical_id is not None and user.logical_id not in cohort
    ]
    by_id = {user.logical_id: user for user in users}
    start = as_of - timedelta(days=365)
    nominations: list[SyntheticNomination] = []
    scenario_plan = _scenario_plan(scenario_active)
    category_ordinals = {category: 0 for category in CATEGORIES}
    background_ordinal = 0

    for segment in range(SEGMENT_COUNT):
        for ordinal in range(NOMINATIONS_PER_SEGMENT):
            global_index = segment * NOMINATIONS_PER_SEGMENT + ordinal
            scenario_event = scenario_plan.get((segment, ordinal))
            family = scenario_event.family if scenario_event else None
            if scenario_event:
                nominator = by_id[scenario_event.nominator_logical_id]
                beneficiary = by_id[scenario_event.beneficiary_logical_id]
            if scenario_event:
                disposition = scenario_event.disposition
                variant = scenario_event.scenario_variant
            else:
                disposition = "LEGITIMATE"
                variant = BACKGROUND_VARIANTS[
                    global_index % len(BACKGROUND_VARIANTS)
                ]
                nominator, beneficiary = _legitimate_parties(
                    active, background_beneficiaries, cohort, global_index,
                    background_ordinal, seed,
                )
                background_ordinal += 1

            # Three is coprime to the five category count, yielding an even
            # category mix inside every temporal segment instead of coupling a
            # fold to one category.
            category_index = (global_index * 3 + segment) % len(CATEGORIES)
            category = CATEGORIES[category_index]
            category_ordinal = category_ordinals[category]
            category_ordinals[category] += 1
            date_position = (
                scenario_event.target_position
                if scenario_event and scenario_event.timing_mode == "ACTIVE"
                else ordinal
            )
            day_in_segment = date_position * 73 // NOMINATIONS_PER_SEGMENT
            nomination_date = start + timedelta(days=segment * 73 + day_in_segment)
            minute = 8 * 60 + ((global_index * 37) % (11 * 60))
            if scenario_event and scenario_event.timing_mode == "ACTIVE":
                if family == "TEMPORAL_BURST":
                    minute = 10 * 60 + scenario_event.timing_ordinal - 1
                else:
                    # Same-day causal order outside the one-hour burst window.
                    minute = 6 * 60 + scenario_event.timing_ordinal * 80
            timestamp = datetime.combine(
                nomination_date,
                time(hour=minute // 60, minute=minute % 60),
                tzinfo=timezone.utc,
            )
            minimum_amount, maximum_amount = CATEGORY_AMOUNT_BOUNDS[category]
            amount_steps = (maximum_amount - minimum_amount) // 50
            amount = minimum_amount + 50 * (
                (global_index * 137) % (amount_steps + 1)
            )

            approver_id = beneficiary.manager_logical_id
            if approver_id is None:
                raise ValueError(
                    f"Beneficiary {beneficiary.logical_id} has no manager"
                )
            status = (
                (
                    "Rejected"
                    if (scenario_event.family_ordinal + segment) % 2 == 0
                    else "Paid"
                )
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
                description=description_for_nomination(
                    category, category_ordinal, seed,
                    beneficiary.display_name, amount,
                ),
                status=status,
                training_disposition=disposition,
                scenario_family=(
                    family if disposition == "FRAUD" else "LEGITIMATE"
                ),
                scenario_variant=variant,
                scenario_id=(scenario_event.scenario_id if scenario_event else None),
                scenario_phase=(scenario_event.phase if scenario_event else "BACKGROUND"),
                context_mode=(
                    scenario_event.context_mode if scenario_event else "NONE"
                ),
                confirmed_patterns=(
                    (family,)
                    if disposition == "FRAUD"
                    and scenario_event is not None
                    and scenario_event.phase == "TARGET"
                    else ()
                ),
            ))
    return _apply_intentional_description_reuse(nominations)


def corpus_hash(users: list[SyntheticUser], nominations: list[SyntheticNomination]) -> str:
    payload = {
        "users": [asdict(user) for user in users],
        "nominations": [asdict(nomination) for nomination in nominations],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
