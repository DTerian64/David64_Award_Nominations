"""
seed_nomination_categories.py
==============================
Seeds dbo.nomination_categories for tenants 1 and 3 with a standard set
of award categories.

Usage (from repo root or scripts/ directory):
    python scripts/seed_nomination_categories.py           # idempotent — skips existing rows
    python scripts/seed_nomination_categories.py --reset   # wipe and re-seed for tenants 1 & 3

Environment variables (same as backend/.env):
    SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD
    DB_DRIVER          optional, defaults to "ODBC Driver 18 for SQL Server"
    USE_MANAGED_IDENTITY  optional, set to "true" for Managed Identity auth
"""

import argparse
import os
import sys
from pathlib import Path

# ── path setup so we can reuse sqlhelper2 ────────────────────────────────────
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "backend"))

from dotenv import load_dotenv
load_dotenv(_repo_root / "backend" / ".env")

from sqlalchemy import text
import sqlhelper2 as db  # noqa: E402  (after sys.path manipulation)

# ── Standard categories seeded for all participating tenants ─────────────────
STANDARD_CATEGORIES = [
    "Innovation & Problem Solving",
    "Teamwork & Collaboration",
    "Leadership & Mentorship",
    "Customer Excellence",
    "Going Above & Beyond",
]

# Tenant IDs to seed
TARGET_TENANTS = [1, 3]


def seed(tenant_id: int, reset: bool) -> None:
    with db.get_db_context() as session:
        if reset:
            deleted = session.execute(
                text("DELETE FROM dbo.nomination_categories WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            session.commit()
            print(f"  [reset] Deleted {deleted.rowcount} existing rows for tenant {tenant_id}.")

        # Fetch existing descriptions to stay idempotent without --reset
        existing = {
            row[0]
            for row in session.execute(
                text("SELECT category_description FROM dbo.nomination_categories WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).fetchall()
        }

        inserted = 0
        for desc in STANDARD_CATEGORIES:
            if desc in existing:
                print(f"  [skip]   tenant {tenant_id} — already has '{desc}'")
                continue
            session.execute(
                text(
                    "INSERT INTO dbo.nomination_categories (tenant_id, category_description) "
                    "VALUES (:tid, :desc)"
                ),
                {"tid": tenant_id, "desc": desc},
            )
            inserted += 1
            print(f"  [insert] tenant {tenant_id} — '{desc}'")

        session.commit()
        print(f"  Done: {inserted} row(s) inserted for tenant {tenant_id}.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed nomination_categories for tenants 1 and 3.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing categories for target tenants before re-seeding.",
    )
    args = parser.parse_args()

    print(f"Seeding nomination_categories for tenants: {TARGET_TENANTS}")
    if args.reset:
        print("  ** --reset: existing rows will be deleted first **\n")

    for tid in TARGET_TENANTS:
        print(f"Tenant {tid}:")
        seed(tid, reset=args.reset)

    print("Seeding complete.")


if __name__ == "__main__":
    main()
