"""
graphrag_seeder.py
==================
Seeds Tenant 1 (Rideshare David64 Organization) with ~10,000 synthetic
nominations designed to embed seven distinct behavioural patterns for
GraphRAG / graph-analytics research:

  1. Nomination rings        — n users each nominating the next in a directed
                               cycle (A→B→C→...→A); sizes 3, 5, 7, and 9.
  2. Super-nominators        — 4 users with nomination volume ≈10× the mean.
  3. Nomination deserts      — 2 manager teams that never appear as nominator
                               or beneficiary in new data.
  4. Approver affinity       — 3 nominator→approver pairs whose nominations are
                               approved at ~80 % vs the ~40 % baseline.
  5. Copy-paste fraud        — 3 template families reused across many
                               nominations (names substituted, structure fixed).
  6. Transactional language  — descriptions citing personal benefit to the
                               nominator ("she helped me deliver my project…").
  7. Hidden candidate        — one fictional person (Jordan Rivera) mentioned
                               positively in many descriptions but never
                               appearing as a beneficiary.

Usage (from the scripts/ directory, or any path):
    python graphrag_seeder.py            # full run
    python graphrag_seeder.py --dry-run  # print plan only, no DB writes
    python graphrag_seeder.py --reset    # delete all rows above the baseline
                                         # (max NominationId at script start), then re-seed

Environment variables (same as the main app):
    SQL_SERVER, SQL_DATABASE
    SQL_USER + SQL_PASSWORD  — or USE_MANAGED_IDENTITY=true

The script is IDEMPOTENT via --reset: running it twice with --reset produces
the same result each time. All user data (names, manager relationships) is
read directly from the database — no CSV files required.
"""

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Backend on sys.path (mirrors seed_tenants.py convention)
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text                          # noqa: E402
from sqlhelper2 import SessionLocal                  # type: ignore[import]

# ---------------------------------------------------------------------------
# Constants that do NOT depend on user data
# ---------------------------------------------------------------------------
TENANT_ID  = 1
CURRENCY   = "USD"

# Manager IDs whose entire teams are "desert" (no new nominations in or out).
DESERT_MANAGER_IDS = {20, 21}

# Super-nominators: 4 employee IDs with very high nomination volume.
SUPER_NOMINATOR_IDS = [45, 113, 178, 247]

# Affinity pairs: (nominator_id, target_approver_id).
# Each nominator consistently picks beneficiaries managed by the target
# approver, and those nominations are approved at ~80 % vs the ~40 % baseline.
AFFINITY_PAIRS = [
    (67,  3),
    (144, 9),
    (233, 15),
]
AFFINITY_NOMINATOR_IDS = {p[0] for p in AFFINITY_PAIRS}

# Fictional person mentioned in descriptions but never nominated.
HIDDEN_CANDIDATE_NAME    = "Jordan Rivera"
HIDDEN_CANDIDATE_USER_ID = 55   # Real user excluded from being a beneficiary

# ---------------------------------------------------------------------------
# User data loading
# ---------------------------------------------------------------------------
def load_users(session) -> dict[int, dict]:
    """
    Return {user_id: {"first": str, "last": str, "manager_id": int|None}}
    for all Tenant 1 users, sourced directly from the database.
    """
    rows = session.execute(
        text(
            "SELECT UserId, FirstName, LastName, ManagerId "
            "FROM Users WHERE TenantId = :tid"
        ),
        {"tid": TENANT_ID},
    ).fetchall()
    return {
        row[0]: {
            "first":      row[1] or "",
            "last":       row[2] or "",
            "manager_id": row[3],
        }
        for row in rows
    }


def build_pools(users: dict[int, dict]) -> dict:
    """
    Derive all user pools from the loaded user map.
    Returns a dict of named pools used throughout the seeder.
    """
    all_managers  = [uid for uid, u in users.items() if u["manager_id"] is None
                     or u["manager_id"] not in users
                     or users[u["manager_id"]]["manager_id"] is None]
    # Approvers = users whose own ManagerId is either NULL or points to the
    # single top-level executive (i.e. they are senior managers/directors).
    # More precisely: approver IDs are the set of ManagerIds used by employees.
    approver_ids: set[int] = {
        u["manager_id"]
        for u in users.values()
        if u["manager_id"] is not None
    }

    # Regular employees = everyone whose manager_id is an approver
    employees = {
        uid: u for uid, u in users.items()
        if u["manager_id"] in approver_ids
    }

    # Teams: approver_id → list of employee user_ids
    teams: dict[int, list[int]] = {}
    for uid, u in employees.items():
        mgr = u["manager_id"]
        teams.setdefault(mgr, []).append(uid)

    # Desert users: members of desert-manager teams
    desert_users: set[int] = set()
    for mgr_id in DESERT_MANAGER_IDS:
        for uid in teams.get(mgr_id, []):
            desert_users.add(uid)

    reserved = desert_users | set(SUPER_NOMINATOR_IDS) | AFFINITY_NOMINATOR_IDS

    # Ring-eligible: employees not in any reserved pool
    ring_eligible = [uid for uid in employees if uid not in reserved]

    # Organic nominators / beneficiaries
    organic_nominators    = [uid for uid in employees
                             if uid not in reserved
                             and uid not in _ring_users(ring_eligible)]
    organic_beneficiaries = [uid for uid in employees
                             if uid not in desert_users
                             and uid != HIDDEN_CANDIDATE_USER_ID]

    return {
        "approver_ids":         approver_ids,
        "employees":            employees,
        "teams":                teams,
        "desert_users":         desert_users,
        "ring_eligible":        ring_eligible,
        "organic_nominators":   organic_nominators,
        "organic_beneficiaries": organic_beneficiaries,
    }


def _ring_users(ring_eligible: list[int]) -> set[int]:
    """Return the set of users that will be consumed by ring building."""
    total_ring_slots = 3*5 + 5*5 + 7*4 + 9*3   # = 15+25+28+27 = 95
    return set(ring_eligible[:total_ring_slots])


def build_rings(ring_eligible: list[int]) -> list[list[int]]:
    """
    Build 17 deterministic rings from the eligible pool.
    Sizes: five 3-rings, five 5-rings, four 7-rings, three 9-rings.
    """
    rng = random.Random(7)
    pool = ring_eligible[:]
    rng.shuffle(pool)
    rings: list[list[int]] = []
    idx = 0
    for size in ([3]*5 + [5]*5 + [7]*4 + [9]*3):
        rings.append(pool[idx: idx + size])
        idx += size
    return rings


# ---------------------------------------------------------------------------
# Description banks
# ---------------------------------------------------------------------------
_AREAS = [
    "cloud infrastructure", "product delivery", "data engineering",
    "API design", "team process improvement", "security hardening",
    "CI/CD pipelines", "stakeholder communication", "QA automation",
    "cost optimisation",
]
_RESULTS = [
    "reducing release cycle time by 30%",
    "cutting incident response time in half",
    "improving team velocity by 20%",
    "enabling a successful quarterly launch",
    "eliminating a critical production risk",
    "saving the team weeks of rework",
    "delivering ahead of the deadline",
    "improving cross-team collaboration significantly",
]

def _organic_desc(first: str, last: str, rng: random.Random) -> str:
    area, result = rng.choice(_AREAS), rng.choice(_RESULTS)
    return rng.choice([
        f"{first} {last} consistently goes above and beyond in {area}, "
        f"{result} for the organisation.",
        f"{first} {last} demonstrated outstanding ownership this period, "
        f"driving {area} work that directly contributed to {result}.",
        f"I want to recognise {first} {last} for exceptional contribution "
        f"to {area}. Their work was instrumental in {result}.",
        f"{first} {last} stepped up during a critical phase of {area}, "
        f"and their effort was central to {result}.",
        f"Throughout this cycle, {first} {last} set a high bar in {area}. "
        f"This directly led to {result}, benefiting the entire team.",
    ])

_TEMPLATE_FAMILIES = [
    (
        "{first} {last} consistently demonstrates exceptional dedication and has been "
        "instrumental in the success of our team deliverables. Their commitment to "
        "excellence and collaborative mindset makes them a standout contributor this period."
    ),
    (
        "I would like to formally recognise {first} {last} for their outstanding "
        "performance. They have consistently exceeded expectations, embodied our core "
        "values, and raised the quality bar across the board. Highly deserving of recognition."
    ),
    (
        "{first} {last} has made a significant positive impact through technical expertise "
        "and a collaborative approach. Their contributions have directly and measurably "
        "improved team outcomes and deserve formal acknowledgement."
    ),
]

def _template_desc(first: str, last: str, family_idx: int) -> str:
    return _TEMPLATE_FAMILIES[family_idx].format(first=first, last=last)

_TRANSACTIONAL_TEMPLATES = [
    "{first} {last} helped me successfully deliver my quarterly goals. "
    "Without their direct support I would not have met my deadline.",
    "Thanks to {first} {last}'s assistance with my deliverable, I was able to "
    "hit my targets this period. I am personally grateful for their help.",
    "{first} {last} stepped in to help me when I was behind on my project. "
    "Their direct involvement made the difference for me.",
    "I could not have completed my assignment without {first} {last}. "
    "They gave up their own time to help me meet my commitments.",
    "{first} {last} supported me personally during a very difficult sprint. "
    "My success this quarter is largely due to their help.",
]

def _transactional_desc(first: str, last: str, rng: random.Random) -> str:
    return rng.choice(_TRANSACTIONAL_TEMPLATES).format(first=first, last=last)

_HIDDEN_TEMPLATES = [
    f"With {HIDDEN_CANDIDATE_NAME}'s mentorship, {{first}} {{last}} has grown "
    f"significantly in their role and delivered outstanding results this period.",
    f"{{first}} {{last}} worked closely with {HIDDEN_CANDIDATE_NAME} on a critical "
    f"initiative and demonstrated exceptional capability throughout.",
    f"Under {HIDDEN_CANDIDATE_NAME}'s coaching, {{first}} {{last}} made remarkable "
    f"progress and is now operating well above expectations.",
    f"{{first}} {{last}}'s recent growth is directly tied to the guidance provided "
    f"by {HIDDEN_CANDIDATE_NAME}. They deserve recognition for their own achievements.",
    f"Following {HIDDEN_CANDIDATE_NAME}'s technical review, {{first}} {{last}} "
    f"redesigned their approach and delivered a far superior outcome.",
]

def _hidden_desc(first: str, last: str, rng: random.Random) -> str:
    return rng.choice(_HIDDEN_TEMPLATES).format(first=first, last=last)


# ---------------------------------------------------------------------------
# Date / status helpers
# ---------------------------------------------------------------------------
_EPOCH_START = datetime(2024, 1, 1)
_EPOCH_END   = datetime(2025, 11, 30)

def _rand_date(rng: random.Random,
               start: datetime = _EPOCH_START,
               end:   datetime = _EPOCH_END) -> datetime:
    delta = end - start
    return start + timedelta(seconds=rng.randint(0, int(delta.total_seconds())))

def _make_status(rng: random.Random,
                 paid_prob:   float = 0.35,
                 reject_prob: float = 0.05) -> str:
    r = rng.random()
    if r < paid_prob:               return "Paid"
    if r < paid_prob + reject_prob: return "Rejected"
    return "Pending"

def _dates_for_status(
        nomination_date: datetime,
        status: str,
        rng: random.Random,
) -> tuple[datetime | None, datetime | None]:
    if status == "Pending":
        return None, None
    approved = nomination_date + timedelta(days=rng.randint(1, 14))
    if status == "Rejected":
        return approved, None
    if status == "Approved":
        return approved, None
    # Paid
    paid = approved + timedelta(days=rng.randint(1, 10))
    return approved, paid


# ---------------------------------------------------------------------------
# Core insertion helper
# ---------------------------------------------------------------------------
def _insert(session,
            next_id:        int,
            nominator_id:   int,
            beneficiary_id: int,
            approver_id:    int,
            amount:         int,
            description:    str,
            nom_date:       datetime,
            status:         str,
            approved_date:  datetime | None,
            payed_date:     datetime | None) -> None:
    notified = nom_date + timedelta(seconds=random.randint(5, 120)) \
               if status != "Pending" or random.random() < 0.7 else None
    session.execute(
        text(
            "INSERT INTO Nominations "
            "(NominationId, NominatorId, BeneficiaryId, ApproverId, Amount, "
            " NominationDescription, NominationDate, Status, "
            " ApprovedDate, PayedDate, Currency, ApproverNotifiedAt) "
            "VALUES "
            "(:nid, :nom, :ben, :apr, :amt, "
            " :desc, :nd, :st, :ad, :pd, :cur, :na)"
        ),
        {
            "nid": next_id, "nom": nominator_id, "ben": beneficiary_id,
            "apr": approver_id, "amt": amount, "desc": description,
            "nd": nom_date, "st": status, "ad": approved_date,
            "pd": payed_date, "cur": CURRENCY, "na": notified,
        }
    )


# ---------------------------------------------------------------------------
# Pattern seeders
# ---------------------------------------------------------------------------
def _seed_organic(session, users, pools, rng, next_id, count, dry_run) -> int:
    print(f"\n[1] Organic background  ({count} nominations)")
    noms = pools["organic_nominators"]
    bens = pools["organic_beneficiaries"]
    inserted = 0
    for _ in range(count):
        nominator   = rng.choice(noms)
        beneficiary = rng.choice([b for b in bens if b != nominator])
        approver    = users[beneficiary]["manager_id"]
        amount      = rng.randint(100, 2_000)
        u           = users[beneficiary]
        desc        = _organic_desc(u["first"], u["last"], rng)
        nom_date    = _rand_date(rng)
        status      = _make_status(rng)
        approved, paid = _dates_for_status(nom_date, status, rng)
        if not dry_run:
            _insert(session, next_id, nominator, beneficiary, approver,
                    amount, desc, nom_date, status, approved, paid)
        next_id += 1; inserted += 1
    print(f"    → {inserted} rows")
    return next_id


def _seed_super_nominators(session, users, pools, rng, next_id, per_nominator, dry_run) -> int:
    print(f"\n[2] Super-nominators  ({len(SUPER_NOMINATOR_IDS)} users × {per_nominator} each)")
    bens = pools["organic_beneficiaries"]
    inserted = 0
    for nominator in SUPER_NOMINATOR_IDS:
        eligible = [b for b in bens if b != nominator]
        for _ in range(per_nominator):
            beneficiary = rng.choice(eligible)
            approver    = users[beneficiary]["manager_id"]
            amount      = rng.randint(200, 1_500)
            u           = users[beneficiary]
            desc        = _organic_desc(u["first"], u["last"], rng)
            nom_date    = _rand_date(rng)
            status      = _make_status(rng)
            approved, paid = _dates_for_status(nom_date, status, rng)
            if not dry_run:
                _insert(session, next_id, nominator, beneficiary, approver,
                        amount, desc, nom_date, status, approved, paid)
            next_id += 1; inserted += 1
    print(f"    → {inserted} rows")
    return next_id


def _seed_rings(session, users, pools, rings, rng, next_id, dry_run) -> int:
    print(f"\n[3] Nomination rings  ({len(rings)} rings, "
          f"{sum(len(r) for r in rings)} ring users)")
    outsider_pool = pools["organic_beneficiaries"]
    inserted = 0
    for ring in rings:
        ring_set = set(ring)
        # Core cycle: each member nominates the next
        for i, nominator in enumerate(ring):
            beneficiary = ring[(i + 1) % len(ring)]
            approver    = users[beneficiary]["manager_id"]
            amount      = rng.randint(300, 1_200)
            u           = users[beneficiary]
            desc        = _organic_desc(u["first"], u["last"], rng)
            nom_date    = _rand_date(rng)
            status      = _make_status(rng, paid_prob=0.50)
            approved, paid = _dates_for_status(nom_date, status, rng)
            if not dry_run:
                _insert(session, next_id, nominator, beneficiary, approver,
                        amount, desc, nom_date, status, approved, paid)
            next_id += 1; inserted += 1
        # Camouflage: 2-3 organic nominations per ring member to outsiders
        outsiders = [b for b in outsider_pool if b not in ring_set]
        for nominator in ring:
            for _ in range(rng.randint(2, 3)):
                beneficiary = rng.choice(outsiders)
                approver    = users[beneficiary]["manager_id"]
                amount      = rng.randint(150, 1_000)
                u           = users[beneficiary]
                desc        = _organic_desc(u["first"], u["last"], rng)
                nom_date    = _rand_date(rng)
                status      = _make_status(rng)
                approved, paid = _dates_for_status(nom_date, status, rng)
                if not dry_run:
                    _insert(session, next_id, nominator, beneficiary, approver,
                            amount, desc, nom_date, status, approved, paid)
                next_id += 1; inserted += 1
    print(f"    → {inserted} rows")
    return next_id


def _seed_approver_affinity(session, users, pools, rng, next_id, per_pair, dry_run) -> int:
    print(f"\n[4] Approver affinity  ({len(AFFINITY_PAIRS)} pairs × {per_pair} nominations)")
    inserted = 0
    for nominator_id, target_approver_id in AFFINITY_PAIRS:
        team = pools["teams"].get(target_approver_id, [])
        team = [uid for uid in team
                if uid not in pools["desert_users"] and uid != nominator_id]
        if not team:
            print(f"    WARNING: no eligible beneficiaries for approver {target_approver_id}")
            continue
        for _ in range(per_pair):
            beneficiary = rng.choice(team)
            approver    = users[beneficiary]["manager_id"]
            amount      = rng.randint(500, 3_000)
            u           = users[beneficiary]
            desc        = _organic_desc(u["first"], u["last"], rng)
            nom_date    = _rand_date(rng)
            status      = _make_status(rng, paid_prob=0.80, reject_prob=0.03)
            approved, paid = _dates_for_status(nom_date, status, rng)
            if not dry_run:
                _insert(session, next_id, nominator_id, beneficiary, approver,
                        amount, desc, nom_date, status, approved, paid)
            next_id += 1; inserted += 1
    print(f"    → {inserted} rows")
    return next_id


def _seed_copy_paste(session, users, pools, rng, next_id, per_family, dry_run) -> int:
    print(f"\n[5] Copy-paste templates  (3 families × {per_family} nominations)")
    noms = pools["organic_nominators"]
    bens = pools["organic_beneficiaries"]
    inserted = 0
    for family_idx in range(3):
        for _ in range(per_family):
            nominator   = rng.choice(noms)
            beneficiary = rng.choice([b for b in bens if b != nominator])
            approver    = users[beneficiary]["manager_id"]
            amount      = rng.randint(300, 1_500)
            u           = users[beneficiary]
            desc        = _template_desc(u["first"], u["last"], family_idx)
            nom_date    = _rand_date(rng)
            status      = _make_status(rng, paid_prob=0.30, reject_prob=0.12)
            approved, paid = _dates_for_status(nom_date, status, rng)
            if not dry_run:
                _insert(session, next_id, nominator, beneficiary, approver,
                        amount, desc, nom_date, status, approved, paid)
            next_id += 1; inserted += 1
    print(f"    → {inserted} rows")
    return next_id


def _seed_transactional(session, users, pools, rng, next_id, count, dry_run) -> int:
    print(f"\n[6] Transactional language  ({count} nominations)")
    noms = pools["organic_nominators"]
    bens = pools["organic_beneficiaries"]
    inserted = 0
    for _ in range(count):
        nominator   = rng.choice(noms)
        beneficiary = rng.choice([b for b in bens if b != nominator])
        approver    = users[beneficiary]["manager_id"]
        amount      = rng.randint(150, 800)
        u           = users[beneficiary]
        desc        = _transactional_desc(u["first"], u["last"], rng)
        nom_date    = _rand_date(rng)
        status      = _make_status(rng, paid_prob=0.28, reject_prob=0.15)
        approved, paid = _dates_for_status(nom_date, status, rng)
        if not dry_run:
            _insert(session, next_id, nominator, beneficiary, approver,
                    amount, desc, nom_date, status, approved, paid)
        next_id += 1; inserted += 1
    print(f"    → {inserted} rows")
    return next_id


def _seed_hidden_candidate(session, users, pools, rings, rng, next_id, count, dry_run) -> int:
    print(f"\n[7] Hidden candidate '{HIDDEN_CANDIDATE_NAME}'  ({count} nominations)")
    ring_users = {uid for ring in rings for uid in ring}
    noms = pools["organic_nominators"] + list(ring_users)
    bens = [b for b in pools["organic_beneficiaries"] if b != HIDDEN_CANDIDATE_USER_ID]
    inserted = 0
    for _ in range(count):
        nominator   = rng.choice(noms)
        beneficiary = rng.choice([b for b in bens if b != nominator])
        approver    = users[beneficiary]["manager_id"]
        amount      = rng.randint(200, 1_200)
        u           = users[beneficiary]
        desc        = _hidden_desc(u["first"], u["last"], rng)
        nom_date    = _rand_date(rng)
        status      = _make_status(rng)
        approved, paid = _dates_for_status(nom_date, status, rng)
        if not dry_run:
            _insert(session, next_id, nominator, beneficiary, approver,
                    amount, desc, nom_date, status, approved, paid)
        next_id += 1; inserted += 1
    print(f"    → {inserted} rows")
    return next_id


# ---------------------------------------------------------------------------
# Reset helper
# ---------------------------------------------------------------------------
def _reset(session, seed_baseline: int, dry_run: bool) -> None:
    count = session.execute(
        text("SELECT COUNT(*) FROM Nominations WHERE NominationId > :b"),
        {"b": seed_baseline},
    ).fetchone()[0]
    print(f"  Found {count} seeded nominations (NominationId > {seed_baseline}).")
    if count == 0 or dry_run:
        if dry_run and count:
            print("  [dry-run] Would delete these rows.")
        return
    session.execute(
        text("DELETE FROM Nominations WHERE NominationId > :b"),
        {"b": seed_baseline},
    )
    session.commit()
    print(f"  Deleted {count} rows.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="GraphRAG pattern seeder for Tenant 1")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan only — do not write to the database")
    parser.add_argument("--reset", action="store_true",
                        help="Delete previously seeded nominations before re-seeding")
    args = parser.parse_args()
    dry_run = args.dry_run

    print("=== GraphRAG Nomination Seeder ===")
    print(f"Mode: {'DRY RUN — no DB writes' if dry_run else 'LIVE'}\n")

    rng = random.Random(42)   # fixed seed → fully reproducible

    session = SessionLocal()
    try:
        # ------------------------------------------------------------------
        # 1. Load users — names AND manager relationships from the database.
        # ------------------------------------------------------------------
        print("Loading users...")
        users = load_users(session)
        print(f"  Loaded {len(users)} Tenant 1 users.")

        # ------------------------------------------------------------------
        # 2. Derive all user pools from actual ManagerId values.
        # ------------------------------------------------------------------
        pools = build_pools(users)
        rings = build_rings(pools["ring_eligible"])
        ring_user_set = {uid for ring in rings for uid in ring}

        # Recompute organic_nominators now that we know ring users
        pools["organic_nominators"] = [
            uid for uid in pools["employees"]
            if uid not in pools["desert_users"]
            and uid not in set(SUPER_NOMINATOR_IDS)
            and uid not in AFFINITY_NOMINATOR_IDS
            and uid not in ring_user_set
        ]

        # ------------------------------------------------------------------
        # Print pool summary
        # ------------------------------------------------------------------
        ring_nom_est = sum(len(r) + len(r) * 2 for r in rings)
        print(f"\nPool summary:")
        print(f"  Approvers              : {len(pools['approver_ids'])}")
        print(f"  Employees              : {len(pools['employees'])}")
        print(f"  Desert users (excluded): {len(pools['desert_users'])}  "
              f"(managers {sorted(DESERT_MANAGER_IDS)})")
        print(f"  Super-nominators       : {len(SUPER_NOMINATOR_IDS)}")
        print(f"  Affinity nominators    : {len(AFFINITY_NOMINATOR_IDS)}")
        print(f"  Ring users             : {len(ring_user_set)}  "
              f"({len(rings)} rings)")
        print(f"  Organic nominator pool : {len(pools['organic_nominators'])}")
        print(f"  Organic beneficiary pool: {len(pools['organic_beneficiaries'])}")

        print(f"\nPlanned insertion:")
        print(f"  1. Organic background      : 7 000")
        print(f"  2. Super-nominators        :   700  (4 × 175)")
        print(f"  3. Nomination rings        : ~{ring_nom_est:4d}  ({len(rings)} rings + camouflage)")
        print(f"  4. Approver affinity       :   300  (3 × 100)")
        print(f"  5. Copy-paste templates    :   300  (3 × 100)")
        print(f"  6. Transactional language  :   300")
        print(f"  7. Hidden candidate        :   160")

        # ------------------------------------------------------------------
        # Snapshot the current high-water mark before any modifications.
        # This becomes the delete threshold for --reset on this and future runs.
        # ------------------------------------------------------------------
        seed_baseline = session.execute(
            text("SELECT ISNULL(MAX(NominationId), 0) FROM Nominations")
        ).fetchone()[0]
        print(f"\nBaseline NominationId (current max): {seed_baseline}")

        if args.reset:
            print("\nStep 0: Reset previously seeded data")
            _reset(session, seed_baseline, dry_run)

        # Re-query after the optional reset so next_id is always accurate.
        max_id = session.execute(
            text("SELECT ISNULL(MAX(NominationId), 0) FROM Nominations")
        ).fetchone()[0]
        next_id = max_id + 1
        start_id = next_id
        print(f"Starting NominationId: {next_id}\n")

        # ------------------------------------------------------------------
        session.execute(text("SET IDENTITY_INSERT Nominations ON"))
        try:
            next_id = _seed_organic(
                session, users, pools, rng, next_id, 7_000, dry_run)
            if not dry_run: session.commit()

            next_id = _seed_super_nominators(
                session, users, pools, rng, next_id, 175, dry_run)
            if not dry_run: session.commit()

            next_id = _seed_rings(
                session, users, pools, rings, rng, next_id, dry_run)
            if not dry_run: session.commit()

            next_id = _seed_approver_affinity(
                session, users, pools, rng, next_id, 100, dry_run)
            if not dry_run: session.commit()

            next_id = _seed_copy_paste(
                session, users, pools, rng, next_id, 100, dry_run)
            if not dry_run: session.commit()

            next_id = _seed_transactional(
                session, users, pools, rng, next_id, 300, dry_run)
            if not dry_run: session.commit()

            next_id = _seed_hidden_candidate(
                session, users, pools, rings, rng, next_id, 160, dry_run)
            if not dry_run: session.commit()

        finally:
            session.execute(text("SET IDENTITY_INSERT Nominations OFF"))
            if not dry_run: session.commit()

        total = next_id - start_id
        print(f"\n{'[DRY RUN] Would insert' if dry_run else '✓ Inserted'} "
              f"{total:,} nominations  "
              f"(NominationId {start_id} – {next_id - 1}).")

    except Exception as exc:
        session.rollback()
        print(f"\n✗ Seeder failed: {exc}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
