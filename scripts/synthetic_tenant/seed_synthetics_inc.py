"""Plan and validate the Synthetics Inc. GNN validation corpus.

Usage (from the repository root)::

    python -m scripts.synthetic_tenant.seed_synthetics_inc --dry-run
    python -m scripts.synthetic_tenant.seed_synthetics_inc --validate
    python -m scripts.synthetic_tenant.seed_synthetics_inc --as-of 2026-09-12 --seed 20260912
    python -m scripts.synthetic_tenant.seed_synthetics_inc --apply-configuration
    python -m scripts.synthetic_tenant.seed_synthetics_inc --apply --manifest-out Output/synthetics-inc-manifest.json

The default is a dry run. ``--apply-configuration`` performs Phase B only: it
clones the approved Tenant 1 settings into the destination SQL tenant. It does
not provision Entra identities, users, nominations, Service Bus messages, or LLM
calls. ``--apply`` performs the guarded, resumable full population workflow.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import uuid

from .scenarios import (
    GENERATOR_NAMESPACE,
    GENERATOR_VERSION,
    corpus_hash,
    generate_nominations,
    generate_users,
)
from .validation import validate_corpus


DEFAULT_SEED = 20260912


def _progress(message: str) -> None:
    print(f"[synthetics] {message}", flush=True)


def _load_environment() -> None:
    """Load the repository's conventional local env files without requiring them."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parents[2]
    for path in (root / ".env", root / "scripts" / ".env", root / "backend" / ".env"):
        load_dotenv(path, override=False)


def build_manifest(seed: int, as_of: date) -> dict:
    users = generate_users(seed)
    nominations = generate_nominations(users, seed, as_of)
    validation = validate_corpus(users, nominations, as_of)
    digest = corpus_hash(users, nominations)
    generation_run_id = str(uuid.uuid5(
        GENERATOR_NAMESPACE, f"run:{seed}:{as_of.isoformat()}:{digest}"
    ))
    return {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "as_of": as_of.isoformat(),
        "corpus_sha256": digest,
        "generation_run_id": generation_run_id,
        "organization": {
            "name": "Synthetics Inc",
            "organization_id": "f74bff31-f42f-4461-a1dd-e1ae978c1abe",
            "host": "synthetic-awards.terianix.ai",
        },
        "validation": validation,
        "persistence": "NOT_REQUESTED",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate and validate the deterministic Synthetics Inc. corpus."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="Generate, validate, and print the manifest."
    )
    mode.add_argument(
        "--validate", action="store_true", help="Alias for the read-only validation run."
    )
    mode.add_argument(
        "--apply-configuration",
        action="store_true",
        help="Apply only the transactional Phase-B SQL tenant configuration.",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Provision configuration, Entra identities, SQL users, and corpus data.",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date.today(),
        help="Exclusive end of the 365-day window (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        help="Write the non-secret apply manifest to this path after success.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.apply or args.apply_configuration:
        _load_environment()
    if args.apply and not args.manifest_out:
        raise ValueError("Full --apply requires --manifest-out for the identity map")
    if args.manifest_out and not args.manifest_out.parent.is_dir():
        raise ValueError(
            f"Manifest directory does not exist: {args.manifest_out.parent}"
        )
    manifest = build_manifest(args.seed, args.as_of)
    users = generate_users(args.seed)
    nominations = generate_nominations(users, args.seed, args.as_of)
    if args.apply_configuration or args.apply:
        from .database import (
            connect_from_environment,
            provision_configuration,
            provision_corpus,
        )

        connection = connect_from_environment()
        try:
            result = provision_configuration(connection)
            corpus_result = None
            directory_result = None
            if args.apply:
                from .entra import client_from_environment, provision_directory

                directory_result = provision_directory(
                    client_from_environment(), users, progress=_progress
                )
                corpus_result = provision_corpus(
                    connection,
                    tenant_id=result.tenant_id,
                    users=users,
                    nominations=nominations,
                    corpus_sha256=manifest["corpus_sha256"],
                    seed=args.seed,
                    generation_run_id=manifest["generation_run_id"],
                    progress=_progress,
                )
        finally:
            connection.close()
        manifest["persistence"] = (
            "CORPUS_APPLIED" if args.apply else "CONFIGURATION_APPLIED"
        )
        manifest["configuration"] = {
            "tenant_id": result.tenant_id,
            "created_tenant": result.created_tenant,
            "category_count": result.category_count,
            "email_template_count": result.email_template_count,
            "graph_pattern_count": result.graph_pattern_count,
            "hashes": result.hashes,
        }
        if directory_result and corpus_result:
            manifest["directory"] = {
                "identity_count": len(directory_result.object_ids_by_upn),
                "created_count": directory_result.created_count,
                "reconciled_count": directory_result.updated_count,
                "manager_count": directory_result.manager_count,
                "admin_role_assigned_now": directory_result.admin_role_assigned,
            }
            manifest["sql_corpus"] = {
                "user_count": corpus_result.sql_user_count,
                "nomination_count": corpus_result.nomination_count,
                "decision_count": corpus_result.decision_count,
                "inserted_nomination_count": corpus_result.inserted_nomination_count,
            }
            manifest["identity_map"] = [
                {
                    "logical_user_id": user.logical_id,
                    "stable_user_id": user.stable_id,
                    "upn": user.upn,
                    "entra_object_id": directory_result.object_ids_by_upn[user.upn],
                    "sql_user_id": corpus_result.sql_user_ids_by_logical_id[
                        user.logical_id
                    ],
                }
                for user in users
            ]
            manifest["identity_map"].append({
                "logical_user_id": "ADMIN",
                "stable_user_id": None,
                "upn": "david64.terian@synthetics.terian-services.com",
                "entra_object_id": directory_result.object_ids_by_upn[
                    "david64.terian@synthetics.terian-services.com"
                ],
                "sql_user_id": corpus_result.admin_sql_user_id,
            })
    if args.manifest_out:
        if not (args.apply or args.apply_configuration):
            raise ValueError("--manifest-out is available only with an apply mode")
        args.manifest_out.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
    console_manifest = {key: value for key, value in manifest.items() if key != "identity_map"}
    print(json.dumps(console_manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
