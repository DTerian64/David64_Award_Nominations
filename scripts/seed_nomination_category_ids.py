"""
seed_nomination_category_ids.py
================================
Seeds CategoryId on existing Nominations for tenants 1 and 3.

The goal is NOT random assignment — random noise teaches the model nothing.
Instead we embed a realistic, learnable fraud signal:

  Signal 1 — Risk-level weighting
  --------------------------------
  Fraudulent nominators reach for vague, unchallengeable categories:
    HIGH / CRITICAL → 80 % "Going Above & Beyond", 20 % "Innovation"
    MEDIUM          → 50 % "Going Above & Beyond", 30 % "Innovation",
                       20 % spread across the remaining three
    LOW / NONE      → evenly distributed across all five categories
    No FraudScore   → evenly distributed across all five categories

  Signal 2 — Nominator consistency
  ---------------------------------
  A lazy fraudster picks one template and repeats it. The random seed for
  each nomination is derived from NominatorId so that every nomination by
  the same nominator lands on the same weighted category. This makes
  category choice consistent *within* a nominator, amplifying the
  correlation with pair_count and concentration_ratio that the RF already
  knows about.

  Combined, the model learns: Category_5 = 1 (tenant 1) or Category_10 = 1
  (tenant 3) is a fraud indicator, especially alongside PairNominationCount
  or HasReciprocalNomination — matching real-world intuition.

Usage:
    python scripts/seed_nomination_category_ids.py           # idempotent — skips already-set rows
    python scripts/seed_nomination_category_ids.py --reset   # clears and re-seeds
    python scripts/seed_nomination_category_ids.py --dry-run # prints plan, no writes

Environment variables (same as backend/.env):
    SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD
    DB_DRIVER  (optional, defaults to "ODBC Driver 18 for SQL Server")
"""

import argparse
import os
import random
import sys
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────────────
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "backend"))

from dotenv import load_dotenv
load_dotenv(_repo_root / "backend" / ".env")

import pyodbc

# ── Category maps ─────────────────────────────────────────────────────────────
# Each entry: (category_id, label) — label is only used for dry-run output.

TENANT_CATEGORIES = {
    1: [
        (1, "Innovation & Problem Solving"),
        (2, "Teamwork & Collaboration"),
        (3, "Leadership & Mentorship"),
        (4, "Customer Excellence"),
        (5, "Going Above & Beyond"),          # ← fraud magnet
    ],
    3: [
        (6,  "Innovation & Problem Solving"),
        (7,  "Teamwork & Collaboration"),
        (8,  "Leadership & Mentorship"),
        (9,  "Customer Excellence"),
        (10, "Going Above & Beyond"),           # ← fraud magnet
    ],
}

# ── Weighted category picker ───────────────────────────────────────────────────

def _pick_category(
    tenant_id: int,
    nominator_id: int,
    risk_level: str | None,
) -> int:
    """
    Return a category_id for one nomination.

    The PRNG is seeded with (tenant_id, nominator_id) so that every
    nomination by the same nominator always resolves to the same category —
    mimicking a real fraudster's copy-paste template behaviour.
    """
    cats = TENANT_CATEGORIES[tenant_id]   # [(id, label), ...]
    rng  = random.Random(tenant_id * 1_000_000 + nominator_id)

    # Indices into cats list — same for both tenants (0-indexed)
    FRAUD_MAGNET  = 4   # "Going Above & Beyond"
    INNOVATION    = 0   # "Innovation & Problem Solving"

    if risk_level in ("HIGH", "CRITICAL"):
        # 80 % fraud magnet, 20 % innovation
        weights = [0.04, 0.04, 0.04, 0.08, 0.80]

    elif risk_level == "MEDIUM":
        # 50 % fraud magnet, 30 % innovation, 20 % spread
        weights = [0.30, 0.05, 0.05, 0.10, 0.50]

    else:
        # LOW / NONE / None → even distribution
        weights = [0.20, 0.20, 0.20, 0.20, 0.20]

    chosen = rng.choices(cats, weights=weights, k=1)[0]
    return chosen[0]   # category id


# ── DB connection ──────────────────────────────────────────────────────────────

def get_conn() -> pyodbc.Connection:
    driver = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={os.getenv('SQL_SERVER')};"
        f"DATABASE={os.getenv('SQL_DATABASE')};"
        f"UID={os.getenv('SQL_USER')};"
        f"PWD={os.getenv('SQL_PASSWORD')};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=60;"
    )
    return pyodbc.connect(conn_str)


# ── Core seeding logic ────────────────────────────────────────────────────────

def seed_tenant(
    conn: pyodbc.Connection,
    tenant_id: int,
    reset: bool,
    dry_run: bool,
) -> None:
    cursor = conn.cursor()
    cats   = TENANT_CATEGORIES[tenant_id]
    cat_ids = {c[0] for c in cats}

    # Optionally clear existing assignments
    if reset and not dry_run:
        cursor.execute(
            "UPDATE dbo.Nominations SET CategoryId = NULL "
            "WHERE CategoryId IN ({})".format(",".join("?" * len(cat_ids))),
            list(cat_ids),
        )
        cleared = cursor.rowcount
        conn.commit()
        print(f"  [reset] Cleared CategoryId from {cleared} nominations for tenant {tenant_id}.")

    # Load all nominations for this tenant — join through Users for tenant isolation
    # Left-join FraudScores to get RiskLevel (NULL if not yet scored)
    cursor.execute("""
        SELECT
            n.NominationId,
            n.NominatorId,
            n.CategoryId,
            fs.RiskLevel
        FROM dbo.Nominations n
        JOIN dbo.Users u ON u.UserId = n.NominatorId
        LEFT JOIN dbo.FraudScores fs ON fs.NominationId = n.NominationId
        WHERE u.TenantId = ?
        ORDER BY n.NominationId
    """, (tenant_id,))
    rows = cursor.fetchall()

    total      = len(rows)
    skipped    = 0
    to_update  = []   # (nomination_id, category_id)

    for nom_id, nominator_id, existing_cat, risk_level in rows:
        if existing_cat is not None and not reset:
            skipped += 1
            continue
        cat_id = _pick_category(tenant_id, nominator_id, risk_level)
        to_update.append((cat_id, nom_id))

    print(f"  Tenant {tenant_id}: {total} nominations — "
          f"{skipped} already set (skipped), {len(to_update)} to update.")

    if dry_run:
        # Print distribution summary
        from collections import Counter
        dist = Counter()
        for cat_id, _ in to_update:
            label = next(lbl for (cid, lbl) in cats if cid == cat_id)
            dist[f"{cat_id} — {label}"] += 1
        print("  Dry-run distribution:")
        for label, count in sorted(dist.items()):
            print(f"    {label}: {count}")
        return

    # Batch UPDATE in chunks of 500
    BATCH = 500
    updated = 0
    for i in range(0, len(to_update), BATCH):
        batch = to_update[i : i + BATCH]
        cursor.executemany(
            "UPDATE dbo.Nominations SET CategoryId = ? WHERE NominationId = ?",
            batch,
        )
        conn.commit()
        updated += len(batch)

    print(f"  Done: {updated} nominations updated for tenant {tenant_id}.")

    # Print final distribution by risk level for verification
    cursor.execute("""
        SELECT
            COALESCE(fs.RiskLevel, 'No score') AS RiskLevel,
            nc.category_description,
            COUNT(*) AS Cnt
        FROM dbo.Nominations n
        JOIN dbo.Users u ON u.UserId = n.NominatorId
        JOIN dbo.nomination_categories nc ON nc.id = n.CategoryId
        LEFT JOIN dbo.FraudScores fs ON fs.NominationId = n.NominationId
        WHERE u.TenantId = ?
          AND n.CategoryId IS NOT NULL
        GROUP BY fs.RiskLevel, nc.category_description
        ORDER BY fs.RiskLevel, nc.category_description
    """, (tenant_id,))
    print(f"\n  Distribution for tenant {tenant_id}:")
    print(f"  {'RiskLevel':<18} {'Category':<35} Count")
    print(f"  {'-'*18} {'-'*35} -----")
    for risk, cat_desc, cnt in cursor.fetchall():
        print(f"  {(risk or 'No score'):<18} {cat_desc:<35} {cnt}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed CategoryId on existing nominations for tenants 1 and 3."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing CategoryId values before re-seeding.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned category distribution without writing to the DB.",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN — no database writes will occur.\n")
    if args.reset:
        print("--reset: existing CategoryId values will be cleared first.\n")

    conn = get_conn()
    try:
        for tenant_id in [1, 3]:
            print(f"Tenant {tenant_id}:")
            seed_tenant(conn, tenant_id, reset=args.reset, dry_run=args.dry_run)
    finally:
        conn.close()

    print("Seeding complete.")


if __name__ == "__main__":
    main()
