"""
sync_holidays.py — Weekly public-holiday refresh stage
======================================================
Keeps dbo.Holidays current so the forecast models' ``is_holiday`` calendar
feature reflects each tenant's country. Country list is derived from every
tenant's Config.locale region (e.g. 'en-US' → 'US'); 'US' is always included.

Source strategy (hybrid):
  1. Fetch from the Nager.Date public API (free, no key) — the "from the
     internet" path.
  2. If the API call fails for a country/year, fall back to the offline Python
     ``holidays`` library so the country still gets populated.

Resilience: dbo.Holidays is the single source of truth the forecast models read.
We only DELETE+reinsert a country's window when we actually obtained rows, so a
total fetch failure leaves the previous data intact and forecasting is never
broken by a network blip.

Env: SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD (same as the other stages).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# Same .env loading as the other stages so this can be run standalone locally.
JOB_DIR = Path(__file__).resolve().parents[1]
env_path = JOB_DIR.parent / ".env"
load_dotenv(env_path)

logger = logging.getLogger("sync_holidays")

# Sync window: a couple of years back (history needs is_holiday too) + next year.
YEARS_BACK = 2
YEARS_AHEAD = 1
NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{cc}"


from utils.db_conn import connect  # noqa: E402 - .env must load before credential setup


def get_db_connection():
    return connect()


def country_from_locale(locale: str) -> str | None:
    """'en-US' → 'US', 'ko-KR' → 'KR'. Returns None if there's no region part."""
    if not locale or "-" not in locale:
        return None
    region = locale.split("-")[-1].strip().upper()
    return region[:2] if len(region) >= 2 else None


def get_tenant_countries(conn) -> set[str]:
    """Distinct ISO country codes across all tenant Config.locale values (+ 'US')."""
    df = pd.read_sql("SELECT Config FROM dbo.Tenants WHERE Config IS NOT NULL", conn)
    countries: set[str] = set()
    for cfg in df["Config"]:
        try:
            cc = country_from_locale(json.loads(cfg).get("locale", ""))
            if cc:
                countries.add(cc)
        except Exception:
            continue
    countries.add("US")   # default tenant / fallback always covered
    return countries


def fetch_nager(cc: str, year: int) -> list[tuple[date, str]] | None:
    """Public holidays for (country, year) from Nager.Date, or None on failure."""
    url = NAGER_URL.format(year=year, cc=cc)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        return [(datetime.strptime(h["date"], "%Y-%m-%d").date(),
                 (h.get("localName") or h.get("name") or "")[:200]) for h in data]
    except Exception as exc:
        logger.warning("Nager fetch failed for %s %d: %s", cc, year, exc)
        return None


def fetch_lib(cc: str, year: int) -> list[tuple[date, str]] | None:
    """Offline fallback via the `holidays` library, or None if unsupported."""
    try:
        import holidays as holidays_lib
        h = holidays_lib.country_holidays(cc, years=year)
        return [(d, str(name)[:200]) for d, name in h.items()]
    except Exception as exc:
        logger.warning("holidays-lib failed for %s %d: %s", cc, year, exc)
        return None


def sync_country(conn, cc: str, years: list[int]) -> int:
    """Refresh one country's holiday window. Returns rows written (0 = left as-is)."""
    by_date: dict[date, tuple[str, str]] = {}   # date -> (name, source)
    for year in years:
        rows = fetch_nager(cc, year)
        source = "nager.date"
        if rows is None:
            rows = fetch_lib(cc, year)
            source = "holidays-lib"
        for d, name in (rows or []):
            by_date.setdefault(d, (name, source))   # first source wins; dedupe by date

    if not by_date:
        logger.warning("No holidays obtained for %s — leaving existing rows intact.", cc)
        return 0

    win_start, win_end = date(min(years), 1, 1), date(max(years), 12, 31)
    cur = conn.cursor()
    # Replace just this country's window (so removed holidays disappear), but only
    # because we have fresh data — a failed fetch above already returned early.
    cur.execute(
        "DELETE FROM dbo.Holidays WHERE CountryCode = ? AND HolidayDate BETWEEN ? AND ?",
        cc, win_start, win_end,
    )
    cur.fast_executemany = True
    cur.executemany(
        "INSERT INTO dbo.Holidays (CountryCode, HolidayDate, Name, Source, UpdatedAt) "
        "VALUES (?, ?, ?, ?, GETUTCDATE())",
        [(cc, d, name, src) for d, (name, src) in sorted(by_date.items())],
    )
    conn.commit()
    logger.info("[%s] synced %d holidays (%d..%d)", cc, len(by_date), min(years), max(years))
    return len(by_date)


def main(tenants_to_process: list | None = None) -> None:  # noqa: ARG001 — not tenant-scoped
    logger.info("Holiday sync stage starting")
    cy = date.today().year
    years = list(range(cy - YEARS_BACK, cy + YEARS_AHEAD + 1))
    conn = get_db_connection()
    try:
        countries = get_tenant_countries(conn)
        logger.info("Syncing holidays for %d countries: %s", len(countries), sorted(countries))
        total = 0
        for cc in sorted(countries):
            try:
                total += sync_country(conn, cc, years)
            except Exception as exc:
                logger.error("[%s] holiday sync failed: %s", cc, exc, exc_info=True)
        logger.info("Holiday sync complete — %d rows upserted across countries", total)
    finally:
        conn.close()


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()
    main()
