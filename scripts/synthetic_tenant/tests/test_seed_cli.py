"""CLI boundary tests for the Synthetics Inc. seeder.

Usage (from the repository root)::

    python -m pytest scripts/synthetic_tenant/tests/test_seed_cli.py -v
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from scripts.synthetic_tenant import database, entra, seed_synthetics_inc


def test_default_identity_uses_the_fixed_v4_window_boundary():
    args = seed_synthetics_inc._parser().parse_args([])

    assert args.seed == 20260921
    assert args.as_of == date(2026, 9, 22)


class _Connection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_apply_corpus_uses_existing_sql_roster_without_microsoft_graph(
    monkeypatch,
    tmp_path,
):
    manifest_path = tmp_path / "v4-manifest.json"
    connection = _Connection()
    captured: dict[str, object] = {}

    monkeypatch.setattr(seed_synthetics_inc, "_load_environment", lambda: None)
    monkeypatch.setattr(
        database,
        "connect_from_environment",
        lambda: connection,
    )
    monkeypatch.setattr(
        database,
        "inspect_existing_configuration",
        lambda _connection: SimpleNamespace(
            tenant_id=5,
            created_tenant=False,
            category_count=5,
            email_template_count=13,
            graph_pattern_count=8,
            hashes={},
        ),
    )
    monkeypatch.setattr(
        database,
        "provision_configuration",
        lambda _connection: (_ for _ in ()).throw(
            AssertionError("--apply-corpus must preserve existing configuration")
        ),
    )

    def _provision_corpus(_connection, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            sql_user_count=401,
            nomination_count=15000,
            decision_count=15000,
            inserted_nomination_count=15000,
            sql_user_ids_by_logical_id={
                user.logical_id: index
                for index, user in enumerate(kwargs["users"], start=1)
            },
            admin_sql_user_id=401,
        )

    monkeypatch.setattr(database, "provision_corpus", _provision_corpus)
    monkeypatch.setattr(
        entra,
        "client_from_environment",
        lambda: (_ for _ in ()).throw(
            AssertionError("Microsoft Graph must not be used by --apply-corpus")
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_synthetics_inc",
            "--apply-corpus",
            "--seed",
            "20260921",
            "--as-of",
            "2026-09-22",
            "--manifest-out",
            str(manifest_path),
        ],
    )

    assert seed_synthetics_inc.main() == 0
    assert connection.closed is True
    assert captured["require_existing_sql_users"] is True

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["persistence"] == "CORPUS_APPLIED"
    assert manifest["configuration"]["status"] == "PRESERVED_VALIDATED"
    assert manifest["directory"] == {
        "expected_identity_count": 401,
        "status": "PRESERVED_NOT_RECONCILED",
    }
    assert len(manifest["sql_identity_map"]) == 401
    assert "identity_map" not in manifest
