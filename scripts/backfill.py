"""
Backfill bhavcopy_cm over a date range.

Usage:
    python scripts/backfill.py --days 30        # last 30 calendar days
    python scripts/backfill.py --from 2025-11-01 --to 2026-05-19

Skips weekends and known holidays via market_hours.is_trading_day.
Idempotent: re-running for already-fetched dates is a fast no-op.

Sleeps 2s between dates to be polite to NSE's archive.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta

from nse_data.collectors.bhavcopy import Bhavcopy
from nse_data.scheduler.market_hours import is_trading_day
from nse_data.session.manager import SessionManager
from nse_data.storage.db import apply_migrations, open_db


log = logging.getLogger(__name__)


def parse_args():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--days", type=int, help="Backfill the last N calendar days")
    g.add_argument("--from", dest="from_", type=str,
                   help="Start date (YYYY-MM-DD), use with --to")
    p.add_argument("--to", type=str,
                   help="End date (YYYY-MM-DD), defaults to today")
    p.add_argument("--db", default="data/nse.db")
    p.add_argument("--sleep", type=float, default=2.0,
                   help="Seconds between dates (politeness)")
    return p.parse_args()


def date_range(start: date, end: date):
    """Yield trading days from start to end inclusive, ascending."""
    d = start
    while d <= end:
        if is_trading_day(d):
            yield d
        d += timedelta(days=1)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = parse_args()

    if args.days:
        end = date.today()
        start = end - timedelta(days=args.days)
    else:
        start = date.fromisoformat(args.from_)
        end = date.fromisoformat(args.to) if args.to else date.today()

    # Migrate the DB once before any runs
    conn = open_db(args.db)
    apply_migrations(conn)
    conn.close()

    session = SessionManager()
    collector = Bhavcopy()

    total_inserted = 0
    total_unchanged = 0
    failures: list[tuple[date, str]] = []

    try:
        for d in date_range(start, end):
            # Per-date fresh connection — same thread-portability hygiene as scheduler
            conn = open_db(args.db)
            try:
                report = collector.run_for_date(session, conn, d)
                if report.errors:
                    err = report.errors[0]
                    log.warning("FAILED %s: %s", d, err.message)
                    failures.append((d, err.message))
                else:
                    log.info(
                        "%s: inserted=%d unchanged=%d rows=%d",
                        d, report.persist.inserted,
                        report.persist.unchanged, report.rows_seen,
                    )
                    total_inserted += report.persist.inserted
                    total_unchanged += report.persist.unchanged
            finally:
                conn.close()
            time.sleep(args.sleep)
    finally:
        session.close()

    log.info("BACKFILL DONE: inserted=%d unchanged=%d failed_dates=%d",
             total_inserted, total_unchanged, len(failures))
    if failures:
        log.info("failed dates:")
        for d, msg in failures:
            log.info("  %s — %s", d, msg)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())