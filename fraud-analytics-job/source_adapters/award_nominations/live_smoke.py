"""Read-only live smoke test for the Award Nomination source adapter.

This module is intentionally not part of the scheduled analytics job or the
ordinary unit-test suite. It connects to the configured Azure SQL database,
loads canonical snapshots, validates them, and prints aggregate diagnostics.

Example
-------
python -m source_adapters.award_nominations.live_smoke \
    --tenant 5 --tenant 1 --window-days 365
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from integrity_data import IntegrityDataset
from source_adapters.award_nominations import AwardNominationAdapter
from source_adapters.contracts import SourceReadRequest


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_environment() -> None:
    job_dir = Path(__file__).resolve().parents[2]
    load_dotenv(job_dir / ".env", override=False)
    load_dotenv(job_dir.parent / ".env", override=False)


def summarize_dataset(dataset: IntegrityDataset) -> dict[str, Any]:
    """Return non-PII aggregate diagnostics for one canonical snapshot."""

    label_provenance = Counter(label.provenance for label in dataset.labels)
    label_dispositions = Counter(label.disposition for label in dataset.labels)
    event_statuses = Counter(event.status or "NULL" for event in dataset.events)
    participant_roles = Counter(
        participant.source_role for participant in dataset.participants
    )
    snapshot = dataset.snapshot
    return {
        "tenant_id": snapshot.tenant_id,
        "source_system": snapshot.source_system,
        "adapter": {
            "name": snapshot.adapter_name,
            "version": snapshot.adapter_version,
        },
        "canonical_schema_version": snapshot.canonical_schema_version,
        "as_of_exclusive": snapshot.as_of_exclusive.isoformat(),
        "window_start_inclusive": (
            snapshot.window_start_inclusive.isoformat()
            if snapshot.window_start_inclusive is not None
            else None
        ),
        "snapshot_id": snapshot.snapshot_id,
        "records_sha256": snapshot.records_sha256,
        "is_synthetic_tenant": snapshot.is_synthetic_tenant,
        "capabilities": sorted(snapshot.capabilities),
        "record_counts": dict(snapshot.record_counts),
        "event_status_counts": dict(sorted(event_statuses.items())),
        "participant_role_counts": dict(sorted(participant_roles.items())),
        "label_provenance_counts": dict(sorted(label_provenance.items())),
        "label_disposition_counts": dict(sorted(label_dispositions.items())),
        "validation": "PASSED",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read and validate Award Nomination canonical snapshots without "
            "training models or writing to the database."
        )
    )
    parser.add_argument(
        "--tenant",
        action="append",
        dest="tenant_ids",
        required=True,
        type=int,
        help="Tenant ID to validate; repeat for multiple tenants.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=365,
        help="History window ending at the exclusive cutoff (default: 365).",
    )
    parser.add_argument(
        "--full-history",
        action="store_true",
        help="Read all history before the cutoff instead of applying a window.",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_utc,
        default=None,
        help="Exclusive ISO-8601 cutoff; defaults to the current UTC time.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.window_days <= 0:
        raise SystemExit("--window-days must be positive")

    _load_environment()
    missing = [name for name in ("SQL_SERVER", "SQL_DATABASE") if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")

    # Import only after .env loading because db_conn constructs its credential at
    # module import time. This command performs SELECTs only and never commits.
    from utils.db_conn import connect

    as_of = args.as_of or datetime.now(timezone.utc)
    adapter = AwardNominationAdapter()
    results: list[dict[str, Any]] = []
    connection = connect()
    try:
        for tenant_id in args.tenant_ids:
            request = SourceReadRequest(
                tenant_id=tenant_id,
                as_of_exclusive=as_of,
                window_days=None if args.full_history else args.window_days,
            )
            results.append(summarize_dataset(adapter.load(connection, request)))
    finally:
        connection.close()

    print(json.dumps({"snapshots": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
