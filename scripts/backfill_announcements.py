"""One-off backfill for raw_announcements over a historical date range.

The live `announcements_equity` collector pulls "current" filings without
date params and runs every 5 minutes. This script reuses the same collector's
normalize() to ingest historical filings by adding from_date / to_date params.

Safe to re-run: insert_ignore dedups on fingerprint.

Usage:
  # Default: 01-Apr-2026 through today
  PYTHONPATH=src python scripts/backfill_announcements.py

  # Custom range
  PYTHONPATH=src python scripts/backfill_announcements.py --from 2026-01-01 --to 2026-03-31

  # Dry run: fetch and normalize, don't write to DB
  PYTHONPATH=src python scripts/backfill_announcements.py --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nse_data.collectors.announcements import Announcements           # noqa: E402
from nse_data.collectors.base import Request                          # noqa: E402
from nse_data.session.manager import SessionManager                   # noqa: E402
from nse_data.storage.models import insert_ignore                     # noqa: E402

DB_PATH = ROOT / "data" / "nse.db"
REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
CHUNK_DAYS = 30  # NSE handles 30 fine (probed at 12,492 rows)


def parse_iso(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def fmt_nse(d: date) -> str:
    """NSE expects DD-MM-YYYY for from_date / to_date."""
    return d.strftime("%d-%m-%Y")


def chunks(start: date, end: date, days: int):
    """Yield (chunk_start, chunk_end) inclusive windows of `days` length."""
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2026-04-01",
                    help="Start date YYYY-MM-DD (default 2026-04-01)")
    ap.add_argument("--to", dest="end", default=date.today().isoformat(),
                    help="End date YYYY-MM-DD (default today)")
    ap.add_argument("--chunk-days", type=int, default=CHUNK_DAYS,
                    help=f"Window size in days (default {CHUNK_DAYS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch & normalize but do not persist")
    args = ap.parse_args()

    start = parse_iso(args.start)
    end = parse_iso(args.end)
    if start > end:
        print(f"ERROR: --from ({start}) is after --to ({end})")
        sys.exit(1)
    if not DB_PATH.exists() and not args.dry_run:
        print(f"ERROR: DB not found at {DB_PATH}")
        sys.exit(1)

    print(f"Backfilling raw_announcements: {start} → {end}")
    print(f"  chunk size: {args.chunk_days} days")
    print(f"  dry run:    {args.dry_run}")
    print()

    collector = Announcements()
    session = SessionManager()
    db = None if args.dry_run else sqlite3.connect(DB_PATH)

    total_fetched = 0
    total_inserted = 0
    total_deduped = 0

    try:
        for chunk_start, chunk_end in chunks(start, end, args.chunk_days):
            req = Request(
                path_or_url="/api/corporate-announcements",
                params={
                    "index": "equities",
                    "from_date": fmt_nse(chunk_start),
                    "to_date": fmt_nse(chunk_end),
                },
                referer=REFERER,
                response_type="json",
            )
            label = f"{chunk_start} → {chunk_end}"
            print(f"[{label}] fetching ...", end=" ", flush=True)

            try:
                data = session.get_json(
                    endpoint_name="backfill_announcements",
                    path=req.path_or_url,
                    referer=req.referer,
                    params=req.params,
                )
            except Exception as exc:
                print(f"FAIL ({type(exc).__name__}: {exc})")
                continue

            if data is None:
                print("empty response, skipping")
                continue

            try:
                rows = collector.normalize(data, req)
            except Exception as exc:
                print(f"NORMALIZE FAIL ({type(exc).__name__}: {exc})")
                continue

            n = len(rows)
            total_fetched += n

            if args.dry_run:
                print(f"OK ({n} rows, not persisted)")
                continue

            # Use the collector's own fingerprint method so the result matches
            # what the live collector would produce. EventCollector's standard
            # insert_ignore path expects rows to carry the fingerprint already
            # (or for the persistence helper to compute it). Check the
            # collector's behavior:
            for row in rows:
                if "fingerprint" not in row:
                    row["fingerprint"] = collector.fingerprint(row)

            result = insert_ignore(
                db, collector.table, rows, key_cols=("fingerprint",)
            )
            db.commit()
            print(f"OK (fetched={n}, inserted={result.inserted}, deduped={result.deduped})")
            total_inserted += result.inserted
            total_deduped += result.deduped

    finally:
        session.close()
        if db is not None:
            db.close()

    print()
    print(f"Done. Across all chunks:")
    print(f"  total fetched:  {total_fetched}")
    print(f"  total inserted: {total_inserted}")
    print(f"  total deduped:  {total_deduped}")


if __name__ == "__main__":
    main()