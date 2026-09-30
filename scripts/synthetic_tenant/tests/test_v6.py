from copy import deepcopy
from datetime import date
import json
import pytest
from scripts.synthetic_tenant.configuration import DEFAULT_PROFILE, load_configuration, configuration_hash
from scripts.synthetic_tenant.generator_v6 import generate
from scripts.synthetic_tenant.audit_v6 import audit, validate, write_bundle
from scripts.synthetic_tenant.scenarios import corpus_hash, generate_users


def test_v6_is_reproducible_preserves_roster_and_explicit_truth():
    c = load_configuration()
    users, rows, design = generate(c, 20260926, date(2026, 9, 27))
    again = generate(c, 20260926, date(2026, 9, 27))
    assert corpus_hash(users, rows) == corpus_hash(again[0], again[1])
    assert users == generate_users(20260912)
    result = validate(users, rows, c, date(2026, 9, 27))
    assert result["fraud_count"] == 300
    assert set(result["confirmed_pattern_counts"].values()) == {50}
    assert len(design["quiet_user_ids"]) == 40
    assert len(design["low_recognition_user_ids"]) == 12
    assert not any(r.beneficiary_logical_id in design["low_recognition_user_ids"] for r in rows)
    assert not any(r.nominator_logical_id in design["quiet_user_ids"] or r.beneficiary_logical_id in design["quiet_user_ids"] for r in rows)


def test_configuration_controls_fraud_budget_and_changes_provenance():
    c = load_configuration()
    bad = deepcopy(c)
    bad["scenarios"]["RING"]["episodes"] += 1
    with pytest.raises(ValueError, match="fraud budget"):
        generate(bad, 1, date(2026, 9, 27))
    other = deepcopy(c)
    other["background"]["working_partners"] = 5
    assert configuration_hash(c) != configuration_hash(other)


def test_larger_population_is_an_offline_configuration_not_a_new_identity_mapping():
    c = load_configuration()
    c["population"]["user_count"] = 800
    users, rows, _ = generate(c, 20260926, date(2026, 9, 27))
    assert validate(users, rows, c, date(2026, 9, 27))["corpus_user_count"] == 800
    assert len({user.upn for user in users}) == 800


def test_unlabelled_class_statistics_are_null_and_partial_audits_never_accept(tmp_path):
    c = load_configuration()
    c["corpus"].update(nomination_count=500, fraud_rate=0)
    for spec in c["scenarios"].values():
        spec["episodes"] = 0
    c["population"].update(quiet_teams=0, low_recognition_users=0)
    c["description_reuse"].update(groups=0, fraud_linked_groups=0)
    users, rows, design = generate(c, 1, date(2026, 9, 27))
    policy = {**c["engine_windows"]["graph_pattern"], "patterns": {}}
    report = audit(users, rows, design, c, date(2026, 9, 27), policy=policy)
    assert report["audit_status"] == "PARTIAL" and not report["acceptance_passed"]
    assert report["tabular_feature_distributions"]["Amount"]["1"] is None
    assert report["pattern_results"]["CopyPaste"]["incidental_findings"] == 0
    write_bundle(tmp_path / "empty-class-audit", c, {}, report, users, rows)


def test_partial_audit_apply_stops_before_any_database_access(monkeypatch):
    from scripts.synthetic_tenant import seed_synthetics_inc as cli
    monkeypatch.setattr("scripts.synthetic_tenant.generator_v6.generate", lambda *args: ([], [], {}))
    monkeypatch.setattr("scripts.synthetic_tenant.audit_v6.validate", lambda *args: {})
    monkeypatch.setattr(cli, "corpus_hash", lambda *args: "test-digest")
    def forbidden_database_access():
        pytest.fail("Partial audit must not load SQL credentials or connect")
    monkeypatch.setattr(cli, "_load_environment", forbidden_database_access)
    args = cli._parser().parse_args(["--config", str(DEFAULT_PROFILE),
                                    "--apply-corpus", "--bundle-out", "unused-v6-apply-test-bundle"])
    with pytest.raises(ValueError, match="complete, accepted audit"):
        cli._run_v6(args)


def test_bundle_is_complete_or_not_published_and_never_overwrites(tmp_path):
    directory = tmp_path / "bundle"
    with pytest.raises(ValueError):
        write_bundle(directory, {}, {}, {"invalid": float("nan")}, [], [])
    assert not directory.exists()
    write_bundle(directory, {"schema_version": 1}, {"seed": 1}, {"audit_status": "PARTIAL"}, [], [])
    assert {p.name for p in directory.iterdir()} == {"configuration.json", "manifest.json", "audit-report.json", "corpus.json"}
    assert json.loads((directory / "manifest.json").read_text())["seed"] == 1
    with pytest.raises(ValueError):
        write_bundle(directory, {}, {}, {}, [], [])
