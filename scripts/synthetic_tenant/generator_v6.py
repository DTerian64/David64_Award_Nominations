"""Behavior-driven generation: persistent working partners plus explicit episodes."""
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import random
import uuid
from .configuration import configuration_hash, validate_configuration
from .descriptions import description_for_nomination
from .scenarios import (CATEGORIES, CATEGORY_AMOUNT_BOUNDS, GENERATOR_NAMESPACE,
                        SyntheticNomination, generate_users)


def generate(configuration: dict, seed: int, as_of: date):
    validate_configuration(configuration)
    c = configuration
    rng = random.Random(seed)
    users = generate_users(c["population"]["directory_seed"], c["population"]["user_count"])
    by_id = {u.logical_id: u for u in users}
    teams = defaultdict(list)
    for u in users:
        if u.manager_logical_id:
            teams[u.manager_logical_id].append(u.logical_id)
    # Preserve the existing four quiet teams for the stable 400-user roster.
    candidates = [key for key in sorted(teams) if len(teams[key]) >= 3 and by_id[key].manager_logical_id]
    from .scenarios import QUIET_TEAM_MANAGER_IDS
    preferred = [key for key in QUIET_TEAM_MANAGER_IDS if key in candidates]
    candidates = preferred + [key for key in candidates if key not in preferred]
    if c["population"]["quiet_teams"] > len(candidates):
        raise ValueError("Not enough teams for the requested desert cohort")
    quiet_managers = candidates[:c["population"]["quiet_teams"]]
    quiet = {key for manager in quiet_managers for key in [manager, *teams[manager]]}
    active = [u for u in users if u.logical_id not in quiet]
    eligible = [u for u in active if u.manager_logical_id]
    count = c["population"]["low_recognition_users"]
    low = {u.logical_id for u in eligible[-count:]} if count else set()
    actors = [u for u in eligible if u.logical_id not in low]
    if len(actors) < 30 or count > len(eligible) - 30:
        raise ValueError("Participation cohorts leave too few scenario actors")
    start = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=c["corpus"]["window_days"])
    drafts = []
    episodes = []
    project_partners = {}
    block_subjects = set()
    def append(source, destination, when, *, family="LEGITIMATE", episode=None, phase="BACKGROUND", positive=False):
        drafts.append({"source": source.logical_id, "destination": destination.logical_id, "when": when,
                       "family": family, "episode": episode, "phase": phase, "positive": positive})

    for family, spec in c["scenarios"].items():
        for index in range(spec["episodes"]):
            # Episode dates cover five chronological bins; fraud and control dates
            # are independently sampled within the same bin, not separate bands.
            for control_number in range(c["controls_per_episode"] + 1):
                positive = control_number == 0
                episode = f"v6:{family}:{index}:{control_number}"
                span = spec["span_days"]
                bin_number = index % 5
                bin_start = int(bin_number * c["corpus"]["window_days"] / 5)
                bin_end = int((bin_number + 1) * c["corpus"]["window_days"] / 5)
                day = rng.randrange(bin_start, max(bin_start + 1, bin_end - span))
                when = start + timedelta(days=day, hours=9)
                party = rng.sample(actors, min(len(actors), 30))
                targets = spec["targets_per_episode"]
                if family == "RING":
                    size = rng.choice(spec["sizes"])
                    edges = [(party[i], party[i + 1]) for i in range(size - 1)]
                    target = (party[size - 1], party[0] if positive else party[size])
                    edges += [target] * targets
                elif family == "RECIPROCAL":
                    edges = [(party[0], party[1]), (party[0], party[2])]
                    edges += [(party[1], party[0] if positive else party[3])] * targets
                elif family in ("SUPER_NOMINATOR", "SUPER_BENEFICIARY", "TEMPORAL_BURST"):
                    volume = spec["event_count"] if positive else max(targets + 2, spec["event_count"] // 3)
                    partners = party[1:]
                    if family == "SUPER_NOMINATOR":
                        edges = [(party[0], partners[i % len(partners)]) for i in range(volume)]
                    elif family == "SUPER_BENEFICIARY":
                        edges = [(partners[i % len(partners)], party[0]) for i in range(volume)]
                    else:
                        recipients, senders = party[:6], party[6:]
                        edges = [(senders[i % len(senders)], recipients[i % len(recipients)]) for i in range(volume)]
                else:
                    available = [u for u in actors if u.logical_id not in block_subjects]
                    if len(available) < spec["left_size"]:
                        raise ValueError("Dense-block episodes require more distinct sender subjects")
                    senders = rng.sample(available, spec["left_size"])
                    party = senders + rng.sample([u for u in actors if u not in senders], min(30 - len(senders), len(actors) - len(senders)))
                    left = party[:spec["left_size"]]
                    right = party[spec["left_size"]:spec["left_size"] + spec["right_size"]]
                    # Shared project relationships apply to both fraud and controls.
                    for u in left:
                        project_partners[u.logical_id] = right
                        block_subjects.add(u.logical_id)
                    edges = [(a, b) for _ in range(spec["repeats"]) for a in left for b in right]
                    if not positive:
                        edges = edges[::2]
                    if len(edges) <= targets:
                        raise ValueError("Dense-block control needs precursor events")
                for edge_index, (source, destination) in enumerate(edges):
                    fraction = edge_index / max(len(edges), 1)
                    seconds = spec.get("burst_hours", span * 24) * 3600 if family == "TEMPORAL_BURST" else span * 86400
                    event_time = when + timedelta(seconds=int(fraction * seconds))
                    is_target = edge_index >= len(edges) - targets
                    append(source, destination, event_time, family=family, episode=episode,
                           phase="TARGET" if is_target else "PRECURSOR", positive=positive and is_target)
                episodes.append({"id": episode, "family": family, "disposition": "FRAUD" if positive else "LEGITIMATE",
                                 "targets": targets, "start": when.isoformat(), "participants": [u.logical_id for u in party if any(u in edge for edge in edges)]})

    reuse_groups = []
    linked_count = c["description_reuse"].get("fraud_linked_groups", 0)
    for group in range(c["description_reuse"]["groups"] - linked_count):
        destination = rng.choice(actors)
        sources = rng.sample([u for u in actors if u != destination], c["description_reuse"]["group_size"])
        episode = f"v6:COPY_PASTE:{group}"
        when = start + timedelta(days=rng.randrange(c["corpus"]["window_days"]), hours=10)
        for i, source in enumerate(sources):
            append(source, destination, when + timedelta(minutes=i), episode=episode, phase="TEXT_REUSE")
        reuse_groups.append(episode)

    # Text reuse can accompany an already-labelled fraud target. It does not
    # assign labels or rewrite the causal topology.
    used_rows = set()
    used_episodes = set()
    linked_groups = []
    for draft in list(drafts):
        if len(linked_groups) == linked_count:
            break
        if not draft["positive"] or draft["episode"] in used_episodes or draft["family"] not in ("SUPER_BENEFICIARY", "BIPARTITE_DENSE_BLOCK", "TEMPORAL_BURST"):
            continue
        candidates = [other for other in drafts if other["episode"] == draft["episode"] and other["destination"] == draft["destination"] and id(other) not in used_rows and other is not draft]
        chosen = [draft]
        for other in candidates:
            if other["source"] not in {item["source"] for item in chosen}:
                chosen.append(other)
            if len(chosen) == c["description_reuse"]["group_size"]:
                break
        if len(chosen) < c["description_reuse"]["group_size"] or id(draft) in used_rows:
            continue
        key = f"v6:COPY_PASTE:fraud-linked:{len(linked_groups)}"
        for item in chosen:
            item["reuse_group"] = key
            used_rows.add(id(item))
        linked_groups.append(key)
        used_episodes.add(draft["episode"])
    if len(linked_groups) != linked_count:
        raise ValueError("Not enough compatible episode rows for fraud-linked description reuse")

    # A stable small set of working relationships replaces independent all-to-all
    # draws. Legitimate repeats and cycles remain possible and are audited.
    partners = {}
    for source in active:
        options = [u for u in actors if u != source]
        same = [u for u in options if u.department == source.department]
        other = [u for u in options if u.department != source.department]
        roster = []
        for _ in range(c["background"]["working_partners"]):
            pool = same if rng.random() < c["background"]["department_affinity"] and same else other or options
            remaining = [u for u in pool if u not in roster] or [u for u in options if u not in roster]
            if not remaining:
                break
            roster.append(rng.choice(remaining))
        partners[source.logical_id] = project_partners.get(source.logical_id, roster)
    remaining_count = c["corpus"]["nomination_count"] - len(drafts)
    coverage = active + [by_id[key] for key in sorted(low) for _ in range(12)]
    if remaining_count < len(coverage):
        raise ValueError("Episode volumes exceed corpus capacity including participation coverage")
    weights = [rng.lognormvariate(0, c["background"]["activity_spread"]) for u in active]
    for i in range(remaining_count):
        source = coverage[i] if i < len(coverage) else rng.choices(active, weights=weights)[0]
        destination = rng.choice(partners[source.logical_id])
        while True:
            when = start + timedelta(days=rng.randrange(c["corpus"]["window_days"]), hours=rng.randrange(8, 18), minutes=rng.randrange(60))
            if when.weekday() < 5 or rng.random() < c["background"]["weekend_weight"]:
                break
        append(source, destination, when)
    drafts.sort(key=lambda row: row["when"])
    positives = sum(row["positive"] for row in drafts)
    categories = {}
    for positive in (False, True):
        indices = [i for i, row in enumerate(drafts) if row["positive"] == positive]
        allocation = [CATEGORIES[i % len(CATEGORIES)] for i in range(len(indices))]
        rng.shuffle(allocation)
        categories.update(zip(indices, allocation))
    namespace = uuid.uuid5(GENERATOR_NAMESPACE, f"v6:{seed}:{as_of}:{configuration_hash(c)}")
    ordinals = Counter()
    rows = []
    reuse_text = {}
    for i, draft in enumerate(drafts):
        category = categories[i]
        minimum, maximum = CATEGORY_AMOUNT_BOUNDS[category]
        high = c["tabular_signals"]["fraud_high_amount_fraction" if draft["positive"] else "legitimate_high_amount_fraction"]
        fraction = rng.uniform(.8, 1) if rng.random() < high else rng.betavariate(2, 6)
        amount = minimum + round((maximum - minimum) * fraction / 50) * 50
        beneficiary = by_id[draft["destination"]]
        description = description_for_nomination(category, ordinals[category] % 3000, seed, beneficiary.display_name, amount)
        ordinals[category] += 1
        if draft["phase"] == "TEXT_REUSE" or draft.get("reuse_group"):
            key = draft.get("reuse_group", draft["episode"])
            if key not in reuse_text:
                reuse_text[key] = category, amount, description
            category, amount, description = reuse_text[key]
        status = "Rejected" if draft["positive"] and rng.random() < c["corpus"]["fraud_rejected_fraction"] else rng.choice(["Paid", "Paid", "Approved"])
        row = SyntheticNomination(logical_id=f"SYN-N{i + 1:06d}", stable_id=str(uuid.uuid5(namespace, str(i))),
            segment=min(4, (draft["when"] - start).days * 5 // c["corpus"]["window_days"]), nomination_time_utc=draft["when"].isoformat(),
            nominator_logical_id=draft["source"], beneficiary_logical_id=draft["destination"], approver_logical_id=beneficiary.manager_logical_id,
            category_key=f"CATEGORY_{CATEGORIES.index(category) + 1:02d}", category_name=category, amount=amount, description=description,
            status=status, training_disposition="FRAUD" if draft["positive"] else "LEGITIMATE",
            scenario_family=draft["family"] if draft["positive"] else "LEGITIMATE", scenario_variant=draft["family"],
            scenario_id=draft["episode"], scenario_phase=draft["phase"], context_mode="ACTIVE" if draft["episode"] else "NONE",
            confirmed_patterns=(draft["family"],) if draft["positive"] else ())
        rows.append(row)
    groups = []
    for key in reuse_groups + linked_groups:
        indices = [i for i, draft in enumerate(drafts) if draft.get("reuse_group") == key or draft["episode"] == key]
        groups.append({"id": key, "logical_nomination_ids": [rows[i].logical_id for i in indices], "fraud_linked": key in linked_groups})
    return users, rows, {"episodes": episodes, "description_reuse_episodes": reuse_groups + linked_groups, "description_reuse_groups": groups,
                         "quiet_managers": quiet_managers, "quiet_user_ids": sorted(quiet), "low_recognition_user_ids": sorted(low),
                         "fraud_count": positives}
