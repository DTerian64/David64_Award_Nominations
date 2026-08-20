"""
synthetic.py — deterministic two-tenant fixture for GNN tests
==============================================================
Generates users and nominations with planted collusion structure, so the graph
builder and the model can be exercised without a database.

The decoy design (this is the whole point)
------------------------------------------
A careless fixture lets flat per-user features give the answer away, and then a
passing test proves nothing about message passing. The first version of this
file made that mistake: scheme members were given extra "camouflage" traffic and
ended up roughly 7x more active than everyone else, so NominationsMade alone
separated the classes and the no-message-passing ablation scored 0.998.

So this fixture equalises every flat signal the model can see:

  * every user makes the SAME number of nominations, so activity counts carry
    nothing;
  * amounts come from one shared distribution, so Amount and AmountZScore
    carry nothing;
  * RING members send ~40% of their nominations to the next member of a
    directed cycle (A->B->C->D->E->A);
  * DECOY users send ~40% of their nominations to a single fixed partner who is
    NOT part of any cycle — a legitimate close collaborator.

Ring and decoy users therefore have near-identical ConcentrationRatio,
UniqueBeneficiaries, ReciprocalPairCount and activity. The ONLY thing that
separates them is whether the concentration chains onward into a cycle — which
is visible at two hops and invisible to any per-user aggregate.

That is precisely the capability ADR-0002 claims a GNN has and the flattened
UserGraphFlags booleans do not, so the ablation in demo_train.py is a real test
of the ADR's premise rather than a formality.

Not covered here: approver-affinity collusion. Distinguishing a colluding
approver from a merely busy one needs its own decoy population; left for a
follow-up fixture.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

TENANT_A = 1
TENANT_B = 2


def make_tenant(
    tenant_id: int,
    n_users: int = 100,
    nominations_per_user: int = 60,
    n_days: int = 400,
    ring_size: int = 8,
    n_decoys: int = 16,
    scheme_share: float = 0.40,
    seed: int = 7,
    user_id_base: int | None = None,
) -> tuple[list[dict], list[dict], list[int]]:
    """
    Return (users, nominations, labels) for one tenant.

    labels[i] is 1 when nominations[i] is a ring hop.

    Every user emits exactly `nominations_per_user` nominations. Ring members
    and decoy users both direct `scheme_share` of theirs at a single fixed
    counterparty; only the ring's counterparties form a closed cycle.
    """
    rng = random.Random(seed + tenant_id)
    base = user_id_base if user_id_base is not None else tenant_id * 1000
    user_ids = [base + i for i in range(n_users)]
    users = [{"UserId": uid, "TenantId": tenant_id, "ManagerId": None} for uid in user_ids]

    start = date(2025, 6, 1)
    ring = user_ids[:ring_size]
    ring_next = {u: ring[(i + 1) % ring_size] for i, u in enumerate(ring)}

    # Decoys form an OPEN CHAIN: D1 -> D2 -> ... -> Dk -> (ordinary user).
    #
    # An earlier version pointed each decoy at an ordinary user. That still
    # leaked: for a ring hop BOTH endpoints were highly concentrated, whereas a
    # decoy hop went concentrated -> diffuse, so the PAIR was separable from flat
    # features even though neither endpoint was. The no-message-passing ablation
    # scored 0.837 on that fixture.
    #
    # A chain fixes it. Every decoy hop is also concentrated -> concentrated, and
    # every decoy's partner also has a concentrated partner. The sole remaining
    # difference is that the ring's chain CLOSES and the decoys' does not — a
    # property no per-user aggregate and no pair of aggregates can express.
    decoys = user_ids[ring_size:ring_size + n_decoys]
    ordinary = user_ids[ring_size + n_decoys:]
    decoy_partner = {u: decoys[i + 1] for i, u in enumerate(decoys[:-1])}
    decoy_partner[decoys[-1]] = rng.choice(ordinary)   # chain end — stays open

    nominations: list[dict] = []
    labels: list[int] = []
    nid = tenant_id * 100_000

    for uid in user_ids:
        for k in range(nominations_per_user):
            is_scheme_hop = rng.random() < scheme_share
            if uid in ring_next and is_scheme_hop:
                beneficiary, label = ring_next[uid], 1
            elif uid in decoy_partner and is_scheme_hop:
                beneficiary, label = decoy_partner[uid], 0
            else:
                beneficiary, label = rng.choice([u for u in user_ids if u != uid]), 0

            approver = rng.choice([u for u in user_ids if u not in (uid, beneficiary)])
            nid += 1
            nominations.append({
                "NominationId":  nid,
                "NominatorId":   uid,
                "BeneficiaryId": beneficiary,
                "ApproverId":    approver,
                "Status":        rng.choice(["Approved", "Paid", "Paid"]),
                "Amount":        round(max(50.0, rng.gauss(500.0, 180.0)), 2),
                "CreatedAt":     start + timedelta(days=rng.randint(0, n_days - 1)),
            })
            labels.append(label)

    order = sorted(range(len(nominations)), key=lambda i: nominations[i]["CreatedAt"])
    nominations = [nominations[i] for i in order]
    labels = [labels[i] for i in order]
    return users, nominations, labels


def make_two_tenants(seed: int = 7):
    ua, na, la = make_tenant(TENANT_A, seed=seed)
    ub, nb, lb = make_tenant(TENANT_B, n_users=50, seed=seed)
    return (ua, na, la), (ub, nb, lb)
