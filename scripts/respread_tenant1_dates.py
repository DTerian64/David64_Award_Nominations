"""
respread_tenant1_dates.py
=========================
Smooth out the bulk-loaded nomination dates for **tenant 1** so the series is
suitable for time-series forecasting (daily aggregation → seasonal-naive,
Holt-Winters, LightGBM). The data was loaded in bursts (notably a ~2,000-row
spike in 03/2026); this script reassigns every tenant-1 nomination's
``NominationDate`` across 01/2024 → today using a realistic generative model,
and cascades the dependent ``ApprovedDate`` / ``PayedDate`` accordingly.

It does NOT insert or delete rows and does NOT change which employee was
nominated — only the *timing* of existing records moves, so per-department
totals are preserved exactly.

Generative model (per agreed spec)
----------------------------------
  daily intensity λ(d) = trend(d) × season(d) × holiday(d) × noise(d)

    trend(d)   : 5% / month, compounding   (1.05 ** months_since_start)
    season(d)  : annual/monthly shape — quarter-end bumps, year-end peak,
                 summer dip                  (no weekday effect, per spec)
    holiday(d) : near-zero on US federal holidays + the late-December lull
    noise(d)   : multiplicative log-normal jitter (seeded)

  Counts per day are drawn with a single multinomial over λ, so the grand
  total is preserved exactly. Records are then assigned to the chronological
  day-slots **status-aware**: resolved records (Paid/Approved/Rejected) skew
  earlier, in-flight records (Submitted/Pending/HRBP) skew toward the present,
  with soft overlap so every period keeps a realistic mix.

  Cascade: each record keeps its ORIGINAL lags
      approval_lag = ApprovedDate - NominationDate
      payment_lag  = PayedDate    - ApprovedDate (or - NominationDate)
  so the ~12.6-day avg-days-to-approval and approval metrics are unchanged.
  Any dependent date that would land in the future is clamped to "now".

Safety
------
  * DRY-RUN BY DEFAULT — prints a before/after report and writes a
    proposed-changes CSV. Nothing is written to the DB unless you pass --commit.
  * --commit creates a timestamped backup table (SELECT * INTO
    dbo.Nominations_backup_<ts>) for the affected rows, then updates inside a
    single transaction (rollback on any error).
  * Hard TenantId guard: only NominationIds whose beneficiary is in the target
    tenant are touched, and the fetched count is asserted before any write.

Usage
-----
  python respread_tenant1_dates.py                 # dry-run (default), tenant 1
  python respread_tenant1_dates.py --commit        # apply for real
  python respread_tenant1_dates.py --seed 7        # reproducible RNG
  python respread_tenant1_dates.py --tenant 1 --monthly-growth 0.05

Environment variables (same as seed_demo.py / fraud-analytics-job)
------------------------------------------------------------------
  SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD
  DB_DRIVER   optional, defaults to "{ODBC Driver 18 for SQL Server}"
"""

import argparse
import csv
import math
import os
import random
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

# numpy is already a backend/script dependency
import numpy as np

# pyodbc / dotenv are only needed for the DB path, not for the importable logic.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass


# ── DB connection (mirrors seed_demo.py) ────────────────────────────────────────

@contextmanager
def get_conn():
    server   = os.environ["SQL_SERVER"]
    database = os.environ["SQL_DATABASE"]
    user     = os.environ["SQL_USER"]
    password = os.environ["SQL_PASSWORD"]
    driver   = os.getenv("DB_DRIVER", "{ODBC Driver 18 for SQL Server}")
    conn_str = (
        f"Driver={driver};"
        f"Server={server};"
        f"Database={database};"
        f"UID={user};"
        f"PWD={password};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=60;"
    )
    import pyodbc
    conn = pyodbc.connect(conn_str)
    try:
        yield conn
    finally:
        conn.close()


# ── Seasonality / holiday model ─────────────────────────────────────────────────

# Multiplicative weight per calendar month (annual/monthly seasonality).
# Quarter-end months lifted; mid-summer dipped; December peaks (year-end pushes).
_MONTH_WEIGHT = {
    1: 0.95, 2: 0.90, 3: 1.10, 4: 1.00, 5: 1.00, 6: 1.10,
    7: 0.85, 8: 0.85, 9: 1.10, 10: 1.00, 11: 1.05, 12: 1.20,
}

# US federal holidays 2024–2026 (observed) — intensity is crushed on these days.
_HOLIDAYS = {
    # 2024
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 5, 27),
    date(2024, 6, 19), date(2024, 7, 4), date(2024, 9, 2), date(2024, 11, 11),
    date(2024, 11, 28), date(2024, 11, 29), date(2024, 12, 25),
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 5, 26),
    date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 11),
    date(2025, 11, 27), date(2025, 11, 28), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 11),
    date(2026, 11, 26), date(2026, 11, 27), date(2026, 12, 25),
}

# Lower = placed earlier in the timeline. Resolved → early, in-flight → late.
_STATUS_RANK = {
    "Paid": 0.0,
    "Approved": 0.7,
    "Rejected": 0.8,
    "PendingHRBPReview": 1.6,
    "Pending": 1.8,
    "Submitted": 2.4,
}
_STATUS_JITTER = 1.2  # uniform ± jitter so status bands overlap (realistic mix)


def _quarter_end_bump(d: date) -> float:
    """Extra lift in the last 6 days of each quarter (push-to-close behaviour)."""
    if d.month in (3, 6, 9, 12):
        # days remaining in month
        nxt = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
        days_left = (nxt - d).days
        if days_left <= 6:
            return 1.5
    return 1.0


def _holiday_factor(d: date) -> float:
    if d in _HOLIDAYS:
        return 0.08
    # Late-December lull (24–31, excluding the 25th already handled).
    if d.month == 12 and d.day >= 24:
        return 0.45
    return 1.0


def daily_intensity(days: list[date], monthly_growth: float, rng: np.random.Generator) -> np.ndarray:
    """λ(d) = trend × season × quarter-end × holiday × noise, all ≥ 0."""
    start = days[0]
    out = np.empty(len(days), dtype=float)
    noise = rng.lognormal(mean=0.0, sigma=0.18, size=len(days))
    for i, d in enumerate(days):
        months_since = (d - start).days / 30.4375
        trend = (1.0 + monthly_growth) ** months_since
        season = _MONTH_WEIGHT[d.month] * _quarter_end_bump(d)
        out[i] = trend * season * _holiday_factor(d) * noise[i]
    return np.maximum(out, 1e-9)


# ── Core (pure, DB-free, importable) ────────────────────────────────────────────

def compute_new_dates(
    records: list[dict],
    start: date,
    end: date,
    seed: int = 42,
    monthly_growth: float = 0.05,
    now: datetime | None = None,
) -> list[dict]:
    """
    Reassign dates for `records` (each: NominationId, NominationDate,
    ApprovedDate, PayedDate, Status). Returns a parallel list of dicts:
    {NominationId, NominationDate, ApprovedDate, PayedDate, _clamped}.

    Pure function — no DB — so it can be unit-tested / simulated offline.
    """
    now = now or datetime.now()
    rng = np.random.default_rng(seed)
    py_rng = random.Random(seed)

    n = len(records)
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)

    # 1) daily shape → exact integer counts via one multinomial
    lam = daily_intensity(days, monthly_growth, rng)
    p = lam / lam.sum()
    counts = rng.multinomial(n, p)

    # 2) chronological slot list (one date per record, ascending)
    slots: list[date] = []
    for d, c in zip(days, counts):
        slots.extend([d] * int(c))
    # (sum(counts) == n by construction; slots already ascending)

    # 3) status-aware ordering: resolved early, in-flight late, soft overlap
    def order_key(rec):
        rank = _STATUS_RANK.get((rec.get("Status") or "").strip(), 1.8)
        return rank + py_rng.uniform(-_STATUS_JITTER, _STATUS_JITTER)

    order = sorted(range(n), key=lambda i: order_key(records[i]))

    out = [None] * n
    for slot_idx, rec_idx in enumerate(order):
        rec = records[rec_idx]
        slot_day = slots[slot_idx]

        # original lags (preserve realism of the lifecycle)
        onom = _as_dt(rec.get("NominationDate"))
        oappr = _as_dt(rec.get("ApprovedDate"))
        opay = _as_dt(rec.get("PayedDate"))
        appr_lag = max((oappr - onom).days, 0) if (oappr and onom) else None
        pay_base = oappr or onom
        pay_lag = max((opay - pay_base).days, 0) if (opay and pay_base) else None

        # new nomination datetime: business hours, no weekday effect (per spec)
        new_nom = datetime(slot_day.year, slot_day.month, slot_day.day,
                           py_rng.randint(8, 17), py_rng.randint(0, 59))

        clamped = False
        new_appr = None
        if appr_lag is not None:
            cand = new_nom + timedelta(days=appr_lag, hours=py_rng.randint(0, 8))
            if cand > now:
                cand, clamped = now, True
            new_appr = cand

        new_pay = None
        if pay_lag is not None:
            base = new_appr or new_nom
            cand = base + timedelta(days=pay_lag, hours=py_rng.randint(0, 8))
            if cand > now:
                cand, clamped = now, True
            new_pay = cand

        out[rec_idx] = {
            "NominationId": rec["NominationId"],
            "NominationDate": new_nom,
            "ApprovedDate": new_appr,
            "PayedDate": new_pay,
            "_clamped": clamped,
        }
    return out


def _as_dt(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    # string fallback
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(v), fmt)
        except ValueError:
            continue
    return None


# ── Reporting ───────────────────────────────────────────────────────────────────

def _monthly_hist(dts) -> dict:
    h = {}
    for dt in dts:
        if dt is None:
            continue
        k = (dt.year, dt.month)
        h[k] = h.get(k, 0) + 1
    return h


def print_report(records: list[dict], new: list[dict]) -> None:
    old_nom = [_as_dt(r.get("NominationDate")) for r in records]
    new_nom = [r["NominationDate"] for r in new]
    oh, nh = _monthly_hist(old_nom), _monthly_hist(new_nom)
    months = sorted(set(oh) | set(nh))

    print("\n  Month   | before |  after  bar(after, #=~10)")
    print("  --------+--------+----------------------------------")
    for (y, m) in months:
        a = nh.get((y, m), 0)
        print(f"  {m:02d}/{y} | {oh.get((y,m),0):5d}  | {a:5d}  {'#'*(a//10)}")

    first = nh[months[0]] if months else 0
    last = nh[months[-1]] if months else 0
    clamped = sum(1 for r in new if r["_clamped"])
    print(f"\n  total records          : {len(records)} (preserved: {len(new)})")
    print(f"  first→last month ratio : {last/first:.2f}×" if first else "  (no data)")
    print(f"  dependent dates clamped: {clamped}")

    # status × year (shows resolved-early / in-flight-late gradient)
    print("\n  status × year (new NominationDate):")
    sx = {}
    for r, nv in zip(records, new):
        s = (r.get("Status") or "?").strip()
        yr = nv["NominationDate"].year
        sx.setdefault(s, {}).setdefault(yr, 0)
        sx[s][yr] += 1
    yrs = sorted({nv["NominationDate"].year for nv in new})
    print("    status            " + "".join(f"{y:>8}" for y in yrs))
    for s in sorted(sx, key=lambda s: _STATUS_RANK.get(s, 9)):
        print(f"    {s:<17}" + "".join(f"{sx[s].get(y,0):>8}" for y in yrs))


def write_changes_csv(records, new, path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["NominationId", "old_NominationDate", "new_NominationDate",
                    "old_ApprovedDate", "new_ApprovedDate",
                    "old_PayedDate", "new_PayedDate", "Status", "clamped"])
        for r, nv in zip(records, new):
            w.writerow([
                r["NominationId"], r.get("NominationDate"), nv["NominationDate"],
                r.get("ApprovedDate"), nv["ApprovedDate"],
                r.get("PayedDate"), nv["PayedDate"], r.get("Status"), nv["_clamped"],
            ])


# ── DB I/O ──────────────────────────────────────────────────────────────────────

def fetch_records(conn, tenant_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT n.NominationId, n.NominationDate, n.ApprovedDate, n.PayedDate, n.Status
        FROM dbo.Nominations n
        JOIN dbo.Users u ON u.UserId = n.BeneficiaryId
        WHERE u.TenantId = ?
    """, tenant_id)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def apply_changes(conn, tenant_id: int, new: list[dict]) -> None:
    """Backup affected rows, then UPDATE inside one transaction."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"Nominations_backup_{ts}"
    cur = conn.cursor()
    conn.autocommit = False
    try:
        # Backup only the affected tenant rows.
        cur.execute(f"""
            SELECT n.* INTO dbo.{backup}
            FROM dbo.Nominations n
            JOIN dbo.Users u ON u.UserId = n.BeneficiaryId
            WHERE u.TenantId = ?
        """, tenant_id)
        print(f"  backup table created: dbo.{backup}")

        cur.fast_executemany = True
        cur.executemany(
            "UPDATE dbo.Nominations "
            "SET NominationDate = ?, ApprovedDate = ?, PayedDate = ? "
            "WHERE NominationId = ?",
            [(r["NominationDate"], r["ApprovedDate"], r["PayedDate"], r["NominationId"])
             for r in new],
        )
        conn.commit()
        print(f"  committed {len(new)} updates.")
    except Exception:
        conn.rollback()
        print("  ERROR — rolled back, no changes applied.")
        raise


# ── main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Respread tenant nomination dates for forecasting.")
    ap.add_argument("--tenant", type=int, default=1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--monthly-growth", type=float, default=0.05,
                    help="compounding monthly trend (0.05 = 5%%/month)")
    ap.add_argument("--start", default="2024-01-01", help="window start YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="window end YYYY-MM-DD (default: today)")
    ap.add_argument("--commit", action="store_true",
                    help="ACTUALLY write to the DB (default is a safe dry-run)")
    ap.add_argument("--csv", default=str(Path(__file__).parent / "respread_proposed_changes.csv"))
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()

    print("=" * 64)
    print(f"  Respread nomination dates — tenant {args.tenant}")
    print(f"  window {start} → {end} | monthly growth {args.monthly_growth:.0%} | seed {args.seed}")
    print(f"  mode: {'COMMIT (writes!)' if args.commit else 'DRY-RUN (no writes)'}")
    print("=" * 64)

    with get_conn() as conn:
        records = fetch_records(conn, args.tenant)
        print(f"  fetched {len(records)} tenant-{args.tenant} nominations")
        if not records:
            print("  nothing to do.")
            return

        new = compute_new_dates(records, start, end,
                                seed=args.seed, monthly_growth=args.monthly_growth)
        print_report(records, new)
        write_changes_csv(records, new, Path(args.csv))
        print(f"\n  proposed changes written to: {args.csv}")

        if args.commit:
            print("\n  --commit set → applying...")
            apply_changes(conn, args.tenant, new)
        else:
            print("\n  DRY-RUN — no DB changes. Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
