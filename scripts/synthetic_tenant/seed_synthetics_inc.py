"""Plan and validate the Synthetics Inc. GNN validation corpus.

Usage (from the repository root)::

    python -m scripts.synthetic_tenant.seed_synthetics_inc --dry-run
    python -m scripts.synthetic_tenant.seed_synthetics_inc --validate
    python -m scripts.synthetic_tenant.seed_synthetics_inc --as-of 2026-09-24 --seed 20260921
    python -m scripts.synthetic_tenant.seed_synthetics_inc --apply-configuration
    python -m scripts.synthetic_tenant.seed_synthetics_inc --apply-corpus --seed 20260921 --as-of 2026-09-24 --manifest-out Output/synthetics-inc-v5-manifest.json
    python -m scripts.synthetic_tenant.seed_synthetics_inc --apply --seed 20260921 --as-of 2026-09-24 --manifest-out Output/synthetics-inc-v5-manifest.json

The default is a dry run. ``--apply-configuration`` performs Phase B only: it
clones the approved Tenant 1 settings into the destination SQL tenant. It does
not provision Entra identities, users, nominations, Service Bus messages, or LLM
calls. ``--apply-corpus`` reconciles existing SQL users and inserts the corpus
without calling Microsoft Graph. ``--apply`` performs the guarded, resumable
full directory and SQL population workflow.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import uuid

from .scenarios import (
    DIRECTORY_SEED,
    GENERATOR_NAMESPACE,
    GENERATOR_VERSION,
    PATTERN_TAXONOMY_VERSION,
    corpus_hash,
    generate_nominations,
    generate_users,
)
from .validation import validate_corpus


DEFAULT_SEED = 20260921
DEFAULT_AS_OF = date(2026, 9, 24)


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
    users = generate_users(DIRECTORY_SEED)
    nominations = generate_nominations(users, seed, as_of)
    validation = validate_corpus(users, nominations, as_of)
    digest = corpus_hash(users, nominations)
    generation_run_id = str(uuid.uuid5(
        GENERATOR_NAMESPACE, f"run:{seed}:{as_of.isoformat()}:{digest}"
    ))
    return {
        "generator_version": GENERATOR_VERSION,
        "pattern_taxonomy_version": PATTERN_TAXONOMY_VERSION,
        "directory_seed": DIRECTORY_SEED,
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
    mode.add_argument(
        "--apply-corpus",
        action="store_true",
        help=(
            "Validate and preserve existing configuration, then provision corpus "
            "data using the complete SQL user roster; do not call Microsoft Graph."
        ),
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--config", type=Path, help="Versioned v6 JSON profile; omit to reproduce the existing v5 corpus.")
    parser.add_argument("--audit", action="store_true", help="Run offline production detector and feature audits for v6.")
    parser.add_argument("--semantic-audit", action="store_true", help="Include full-corpus semantic features and CopyPaste using cached local model weights.")
    parser.add_argument("--graph-policy", type=Path, help="Published inference-snapshot JSON/gzip, or its scoring_policy JSON, for v6 audit parity.")
    parser.add_argument("--export-graph-policy", type=Path, help="Read Tenant 5's active SQL Graph policy into a fresh local JSON file; no SQL writes.")
    parser.add_argument("--bundle-out", type=Path, help="Fresh directory for configuration, corpus, manifest and audit report together.")
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=DEFAULT_AS_OF,
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
    if args.export_graph_policy:
        if args.apply or args.apply_corpus or args.apply_configuration or args.config or args.audit or args.bundle_out:
            raise ValueError("Export the Graph policy as a separate read-only command")
        if args.export_graph_policy.exists():
            raise ValueError("Refusing to overwrite an exported policy")
        _load_environment()
        from .audit_v6 import _production_imports
        _production_imports()
        from .database import connect_from_environment, inspect_existing_configuration
        from systems.award_nominations.modeling.graph import _load_active_graph_policy
        connection = connect_from_environment()
        try:
            destination = inspect_existing_configuration(connection)
            policy = _load_active_graph_policy(connection, destination.tenant_id, 180)
        finally:
            connection.close()
        args.export_graph_policy.write_text(json.dumps(policy, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Exported Tenant {destination.tenant_id} Graph policy to {args.export_graph_policy}")
        return 0
    if args.config:
        return _run_v6(args)
    if args.audit or args.semantic_audit or args.graph_policy or args.bundle_out:
        raise ValueError("v6 audit/bundle options require --config")
    if args.apply or args.apply_corpus or args.apply_configuration:
        _load_environment()
    if (args.apply or args.apply_corpus) and not args.manifest_out:
        raise ValueError("Corpus apply requires --manifest-out")
    if args.manifest_out and not args.manifest_out.parent.is_dir():
        raise ValueError(
            f"Manifest directory does not exist: {args.manifest_out.parent}"
        )
    manifest = build_manifest(args.seed, args.as_of)
    users = generate_users(DIRECTORY_SEED)
    nominations = generate_nominations(users, args.seed, args.as_of)
    if args.apply_configuration or args.apply or args.apply_corpus:
        from .database import (
            connect_from_environment,
            inspect_existing_configuration,
            provision_configuration,
            provision_corpus,
        )

        connection = connect_from_environment()
        try:
            result = (
                inspect_existing_configuration(connection)
                if args.apply_corpus
                else provision_configuration(connection)
            )
            corpus_result = None
            directory_result = None
            if args.apply:
                from .entra import client_from_environment, provision_directory

                directory_result = provision_directory(
                    client_from_environment(), users, progress=_progress
                )
            if args.apply or args.apply_corpus:
                corpus_result = provision_corpus(
                    connection,
                    tenant_id=result.tenant_id,
                    users=users,
                    nominations=nominations,
                    corpus_sha256=manifest["corpus_sha256"],
                    seed=args.seed,
                    generation_run_id=manifest["generation_run_id"],
                    require_existing_sql_users=args.apply_corpus,
                    progress=_progress,
                )
        finally:
            connection.close()
        manifest["persistence"] = (
            "CORPUS_APPLIED"
            if args.apply or args.apply_corpus
            else "CONFIGURATION_APPLIED"
        )
        manifest["configuration"] = {
            "status": (
                "PRESERVED_VALIDATED"
                if args.apply_corpus else "RECONCILED"
            ),
            "tenant_id": result.tenant_id,
            "created_tenant": result.created_tenant,
            "category_count": result.category_count,
            "email_template_count": result.email_template_count,
            "graph_pattern_count": result.graph_pattern_count,
            "hashes": result.hashes,
        }
        if corpus_result:
            if directory_result:
                manifest["directory"] = {
                    "status": "RECONCILED",
                    "identity_count": len(directory_result.object_ids_by_upn),
                    "created_count": directory_result.created_count,
                    "reconciled_count": directory_result.updated_count,
                    "manager_count": directory_result.manager_count,
                    "admin_role_assigned_now": directory_result.admin_role_assigned,
                }
            else:
                manifest["directory"] = {
                    "status": "PRESERVED_NOT_RECONCILED",
                    "expected_identity_count": 401,
                }
            manifest["sql_corpus"] = {
                "user_count": corpus_result.sql_user_count,
                "nomination_count": corpus_result.nomination_count,
                "decision_count": corpus_result.decision_count,
                "inserted_nomination_count": corpus_result.inserted_nomination_count,
            }
            manifest["sql_identity_map"] = [
                {
                    "logical_user_id": user.logical_id,
                    "stable_user_id": user.stable_id,
                    "upn": user.upn,
                    "sql_user_id": corpus_result.sql_user_ids_by_logical_id[
                        user.logical_id
                    ],
                }
                for user in users
            ]
            manifest["sql_identity_map"].append({
                "logical_user_id": "ADMIN",
                "stable_user_id": None,
                "upn": "david64.terian@synthetics.terian-services.com",
                "sql_user_id": corpus_result.admin_sql_user_id,
            })
            if directory_result:
                manifest["identity_map"] = [
                    {
                        **item,
                        "entra_object_id": directory_result.object_ids_by_upn[
                            item["upn"]
                        ],
                    }
                    for item in manifest["sql_identity_map"]
                ]
    if args.manifest_out:
        if not (args.apply or args.apply_corpus or args.apply_configuration):
            raise ValueError("--manifest-out is available only with an apply mode")
        args.manifest_out.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
    console_manifest = {
        key: value
        for key, value in manifest.items()
        if key not in ("identity_map", "sql_identity_map")
    }
    print(json.dumps(console_manifest, indent=2, sort_keys=True))
    return 0


def _run_v6(args) -> int:
    from .configuration import load_configuration, configuration_hash
    from .generator_v6 import generate
    from .audit_v6 import audit, validate, write_bundle, _production_imports
    configuration = load_configuration(args.config)
    if args.apply or args.apply_configuration:
        raise ValueError("v6 uses --apply-corpus to preserve the existing directory and configuration; full directory/configuration provisioning remains a separate operation")
    if args.apply_corpus and not args.bundle_out:
        raise ValueError("v6 corpus apply requires --bundle-out")
    if args.manifest_out:
        raise ValueError("v6 publishes its manifest with --bundle-out, not as a separate file")
    if args.bundle_out and args.bundle_out.exists():
        raise ValueError("Bundle output must be a fresh directory")
    if args.semantic_audit and not args.audit:
        raise ValueError("--semantic-audit requires --audit")
    policy = None
    if args.graph_policy:
        import gzip
        raw = gzip.decompress(args.graph_policy.read_bytes()).decode() if args.graph_policy.suffix == ".gz" else args.graph_policy.read_text(encoding="utf-8")
        payload = json.loads(raw)
        policy = payload.get("scoring_policy", payload)
    users, nominations, design = generate(configuration, args.seed, args.as_of)
    validation = validate(users, nominations, configuration, args.as_of)
    digest = corpus_hash(users, nominations)
    run_id = str(uuid.uuid5(GENERATOR_NAMESPACE, f"v6:{digest}"))
    report = (audit(users, nominations, design, configuration, args.as_of, policy=policy, semantic=args.semantic_audit)
              if args.audit else {"audit_status": "NOT_RUN", "acceptance_passed": False})
    manifest = {"generator_version": configuration["generator_version"], "pattern_taxonomy_version": PATTERN_TAXONOMY_VERSION,
                "configuration_sha256": configuration_hash(configuration), "corpus_sha256": digest,
                "directory_seed": configuration["population"]["directory_seed"], "seed": args.seed, "as_of": args.as_of.isoformat(),
                "generation_run_id": run_id, "validation": validation, "design": design, "persistence": "NOT_REQUESTED",
                "audit_status": report["audit_status"], "acceptance_passed": report["acceptance_passed"]}
    if args.apply_corpus:
        if not report["acceptance_passed"]:
            raise ValueError("v6 apply requires a complete, accepted audit against a supplied published policy and local semantic model")
        if len(users) != 400 or configuration["population"]["directory_seed"] != DIRECTORY_SEED:
            raise ValueError("Tenant 5 apply must preserve its approved 400-user roster; larger/custom populations are offline only")
        _load_environment()
        from .database import connect_from_environment, inspect_existing_configuration, provision_corpus
        _production_imports()
        from systems.award_nominations.modeling.graph import _load_active_graph_policy
        connection = connect_from_environment()
        try:
            result = inspect_existing_configuration(connection)
            current_policy = _load_active_graph_policy(connection, result.tenant_id, 180)
            if current_policy != policy:
                raise ValueError("Supplied audit policy does not match the current SQL policy; export the new snapshot and audit again")
            corpus = provision_corpus(connection, tenant_id=result.tenant_id, users=users, nominations=nominations,
                                      corpus_sha256=digest, seed=args.seed, generation_run_id=run_id,
                                      generator_version=configuration["generator_version"], require_existing_sql_users=True, progress=_progress)
            manifest["persistence"] = "CORPUS_APPLIED"
            manifest["sql_corpus"] = {"tenant_id": result.tenant_id, "user_count": corpus.sql_user_count,
                                      "nomination_count": corpus.nomination_count, "decision_count": corpus.decision_count}
        finally:
            connection.close()
    if args.bundle_out:
        if policy is not None:
            report["published_scoring_policy"] = policy
        write_bundle(args.bundle_out, configuration, manifest, report, users, nominations)
    print(json.dumps({key: value for key, value in manifest.items() if key != "design"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
