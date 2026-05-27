"""Run collectors once, by hand — catch-up and ad-hoc tool.

The scheduler only runs while the laptop is on, so daily/weekly feeds scheduled
for an evening the machine was asleep get missed. This runs them on demand.

    # run the daily/weekly collectors that are currently stale (same verdict
    # the health dashboard shows), e.g. after the laptop was off overnight:
    python scripts/run_collectors.py --due

    # run specific collectors by their endpoints.yaml name:
    python scripts/run_collectors.py fii_dii volatility surveillance_gsm

    # just list what's due without running anything:
    python scripts/run_collectors.py --due --dry-run

Caveat: snapshot endpoints serve only the latest day, so a run missed two days
ago captures *today's* value, not the gap. This recovers a missed schedule, not
lost history.
"""

from __future__ import annotations

import argparse
import sys

from nse_data.scheduler.catchup import due_collectors, run_due
from nse_data.session.manager import SessionManager
from nse_data.settings import load_endpoints
from nse_data.storage.cache import MemoryDedupCache
from nse_data.storage.db import open_db

DB_PATH = "data/nse.db"
ENDPOINTS = "config/endpoints.yaml"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("names", nargs="*", help="endpoints.yaml collector names to run")
    p.add_argument("--due", action="store_true",
                   help="run all daily/weekly collectors whose data is stale")
    p.add_argument("--dry-run", action="store_true",
                   help="with --due, print what would run and exit")
    args = p.parse_args()

    if not args.names and not args.due:
        p.error("give collector names, or --due")

    endpoints = load_endpoints(ENDPOINTS)
    conn = open_db(DB_PATH)

    if args.due and args.dry_run:
        due = due_collectors(conn, endpoints)
        print("due:", ", ".join(due) if due else "(none)")
        conn.close()
        return 0

    names = args.names or None  # None => auto-detect due set
    session = SessionManager()
    try:
        ran = run_due(session, DB_PATH, endpoints, MemoryDedupCache(), conn, names=names)
        print(f"ran: {', '.join(ran) if ran else '(none)'}")
    finally:
        session.close()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
