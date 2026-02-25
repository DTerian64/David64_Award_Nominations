"""
agents/exporters/csv_writer.py
───────────────────────────────
Builds an in-memory CSV from data rows.
Returns (bytes, filename) — no I/O, no blob, purely functional.
"""

import csv
import io
from datetime import datetime


def build_csv(
    rows: list[dict],
    filename: str | None = None,
) -> tuple[bytes, str]:
    """
    Serialise rows to UTF-8 CSV bytes.
    Returns (csv_bytes, filename).
    """
    fname = (filename or f"analytics_data_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}") + ".csv"

    if not rows:
        return b"", fname

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

    return output.getvalue().encode("utf-8"), fname