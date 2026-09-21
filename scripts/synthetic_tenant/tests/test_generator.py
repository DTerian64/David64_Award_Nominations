"""Validate the deterministic Synthetics Inc. logical corpus.

Usage (from the repository root)::

    python -m pytest scripts/synthetic_tenant/tests/test_generator.py -v
"""

from datetime import date

from scripts.synthetic_tenant.scenarios import (
    CATEGORIES,
    CATEGORY_AMOUNT_BOUNDS,
    CONTROL_FAMILY_BY_VARIANT,
    DIRECTORY_SEED,
    SPECIALIST_FAMILIES,
    corpus_hash,
    generate_nominations,
    generate_users,
)
from scripts.synthetic_tenant.seed_synthetics_inc import build_manifest
from scripts.synthetic_tenant.validation import validate_corpus


AS_OF = date(2026, 9, 22)
SEED = 20260921


def _corpus():
    users = generate_users(DIRECTORY_SEED)
    nominations = generate_nominations(users, SEED, AS_OF)
    return users, nominations


def test_exact_population_labels_segments_and_scenarios():
    users, nominations = _corpus()

    result = validate_corpus(users, nominations, AS_OF)

    assert result["corpus_user_count"] == 400
    assert result["total_directory_and_sql_users"] == 401
    assert result["active_graph_participants"] == 360
    assert result["nomination_count"] == 15_000
    assert result["legitimate_count"] == 14_700
    assert result["fraud_count"] == 300
    assert result["rolling_train_legitimate_count"] == 8_820
    assert result["rolling_train_fraud_count"] == 180
    assert result["holdout_legitimate_count"] == 2_940
    assert result["holdout_fraud_count"] == 60
    assert result["causal_scenario_count"] == 600
    assert result["causal_precursor_count"] == 4_520
    assert result["active_context_target_count"] == 180
    assert result["established_context_target_count"] == 120
    assert result["fraud_scenarios"] == {
        family: 50 for family in sorted(SPECIALIST_FAMILIES)
    }
    assert result["confirmed_pattern_counts"] == {
        family: 50 for family in sorted(SPECIALIST_FAMILIES)
    }
    assert result["legitimate_control_counts"] == {
        family: 50 for family in sorted(SPECIALIST_FAMILIES)
    }
    assert all(
        evidence == {
            "training_positive_count": 30,
            "selection_fold_positive_counts": [10, 10],
            "final_holdout_positive_count": 10,
        }
        for evidence in result["fold_label_evidence"].values()
    )
    assert result["category_counts"] == {
        category: 3_000 for category in sorted(CATEGORIES)
    }
    assert all(
        CATEGORY_AMOUNT_BOUNDS[row.category_name][0]
        <= row.amount
        <= CATEGORY_AMOUNT_BOUNDS[row.category_name][1]
        for row in nominations
    )
    assert all(
        row.training_disposition == "FRAUD"
        for row in nominations
        if row.status == "Rejected"
    )
    assert any(
        row.training_disposition == "FRAUD" and row.status in ("Approved", "Paid")
        for row in nominations
    )


def test_every_fraud_and_control_target_has_earlier_causal_precursors():
    _users, nominations = _corpus()
    by_scenario = {}
    for row in nominations:
        if row.scenario_id:
            by_scenario.setdefault(row.scenario_id, []).append(row)

    assert len(by_scenario) == 600
    for rows in by_scenario.values():
        target = next(row for row in rows if row.scenario_phase == "TARGET")
        precursors = [
            row for row in rows if row.scenario_phase == "PRECURSOR"
        ]
        assert target.training_disposition in {"FRAUD", "LEGITIMATE"}
        assert len(precursors) >= 2
        assert all(
            row.training_disposition == "LEGITIMATE" for row in precursors
        )
        assert all(
            row.nomination_time_utc < target.nomination_time_utc
            for row in precursors
        )


def test_labels_are_direct_singletons_without_family_mapping():
    _users, nominations = _corpus()

    for row in nominations:
        if row.training_disposition == "FRAUD":
            assert row.scenario_family in SPECIALIST_FAMILIES
            assert row.confirmed_patterns == (row.scenario_family,)
        else:
            assert row.scenario_family == "LEGITIMATE"
            assert row.confirmed_patterns == ()

    control_targets = [
        row for row in nominations
        if row.scenario_phase == "TARGET"
        and row.training_disposition == "LEGITIMATE"
        and row.context_mode != "NONE"
    ]
    assert len(control_targets) == 300
    assert all(
        family not in f"{row.scenario_id}:{row.scenario_variant}"
        for row in control_targets
        for family in SPECIALIST_FAMILIES
    )


def test_established_and_active_targets_have_different_temporal_contexts():
    _users, nominations = _corpus()
    by_scenario = {}
    for row in nominations:
        if row.scenario_id:
            by_scenario.setdefault(row.scenario_id, []).append(row)

    for rows in by_scenario.values():
        target = next(row for row in rows if row.scenario_phase == "TARGET")
        precursors = [
            row for row in rows if row.scenario_phase == "PRECURSOR"
        ]
        if target.context_mode == "ACTIVE":
            assert all(row.segment == target.segment for row in precursors)
        else:
            assert target.context_mode == "ESTABLISHED"
            established = [
                row for row in precursors
                if row.segment <= max(0, target.segment - 2)
            ]
            active = [row for row in precursors if row.segment == target.segment]
            assert established
            behavior = (
                target.scenario_family
                if target.training_disposition == "FRAUD"
                else CONTROL_FAMILY_BY_VARIANT[target.scenario_variant]
            )
            if behavior == "TEMPORAL_BURST":
                assert len(active) >= 3
            else:
                assert not active


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


def test_directory_roster_remains_stable_across_corpus_versions():
    users = generate_users(DIRECTORY_SEED)

    assert DIRECTORY_SEED == 20260912
    assert users[0].logical_id == "SYN-U0001"
    assert users[0].upn == "shoghik.hakobyan@synthetics.terian-services.com"
