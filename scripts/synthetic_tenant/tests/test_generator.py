"""Validate the deterministic Synthetics Inc. logical corpus.

Usage (from the repository root)::

    python -m pytest scripts/synthetic_tenant/tests/test_generator.py -v
"""

from datetime import date

from scripts.synthetic_tenant.scenarios import (
    CATEGORIES,
    CATEGORY_AMOUNT_BOUNDS,
    corpus_hash,
    generate_nominations,
    generate_users,
)
from scripts.synthetic_tenant.seed_synthetics_inc import build_manifest
from scripts.synthetic_tenant.validation import validate_corpus


AS_OF = date(2026, 9, 12)
SEED = 20260912


def _corpus():
    users = generate_users(SEED)
    nominations = generate_nominations(users, SEED, AS_OF)
    return users, nominations


def test_exact_population_labels_segments_and_scenarios():
    users, nominations = _corpus()

    result = validate_corpus(users, nominations, AS_OF)

    assert result["corpus_user_count"] == 400
    assert result["total_directory_and_sql_users"] == 401
    assert result["active_graph_participants"] == 360
    assert result["nomination_count"] == 5_000
    assert result["legitimate_count"] == 4_900
    assert result["fraud_count"] == 100
    assert result["rolling_train_legitimate_count"] == 2_940
    assert result["rolling_train_fraud_count"] == 60
    assert result["holdout_legitimate_count"] == 980
    assert result["holdout_fraud_count"] == 20
    assert result["fraud_scenarios"] == {
        "AMOUNT": 10,
        "BURST": 15,
        "CONCENTRATION": 20,
        "MIXED": 5,
        "RECIPROCAL": 20,
        "RING": 30,
    }
    assert result["category_counts"] == {
        category: 1_000 for category in sorted(CATEGORIES)
    }
    assert all(
        CATEGORY_AMOUNT_BOUNDS[row.category_name][0]
        <= row.amount
        <= CATEGORY_AMOUNT_BOUNDS[row.category_name][1]
        for row in nominations
    )


def test_same_seed_and_as_of_produce_the_same_corpus_hash():
    users_a, nominations_a = _corpus()
    users_b, nominations_b = _corpus()

    assert corpus_hash(users_a, nominations_a) == corpus_hash(users_b, nominations_b)
    assert build_manifest(SEED, AS_OF)["corpus_sha256"] == corpus_hash(
        users_a, nominations_a
    )


def test_administrator_is_not_in_the_generated_corpus():
    users, nominations = _corpus()
    forbidden = "david64.terian@synthetics.terian-services.com"

    assert forbidden not in {user.upn for user in users}
    corpus_ids = {user.logical_id for user in users}
    assert all(row.nominator_logical_id in corpus_ids for row in nominations)
    assert all(row.beneficiary_logical_id in corpus_ids for row in nominations)
