"""
Compute registered indicators across the symbol universe.

    # all cadences (EOD daily across full universe + intraday across FNO+N500)
    python scripts/backfill_indicators.py

    # only one cadence
    python scripts/backfill_indicators.py --cadence eod
    python scripts/backfill_indicators.py --cadence intraday

    # specific symbols (overrides universe selection)
    python scripts/backfill_indicators.py --symbols RELIANCE,TCS

Idempotent: each indicator runs incrementally, so re-running only writes
rows that didn't exist yet. Safe to invoke after a nightly bhavcopy load,
or once after applying the indicator_* migrations to populate history.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter

from nse_data.indicators.compute import run_all
from nse_data.indicators.registry import INDICATORS
from nse_data.indicators.universe import all_equity_symbols, fno_plus_nifty500
from nse_data.storage.db import open_db


def _universe_for(conn, cadence: str | None) -> list[str]:
    """Pick the right universe for the cadence. Intraday gets the tradable
    subset (FNO ∪ Nifty 500); EOD and full backfills get every EQ symbol."""
    if cadence == "intraday":
        return fno_plus_nifty500(conn)
    return all_equity_symbols(conn)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument(
        "--cadence", choices=["eod", "intraday", "session"],
        help="Filter to one cadence. Default: run all cadences.",
    )
    ap.add_argument(
        "--symbols",
        help="Comma-separated symbols. Overrides the cadence-based universe.",
    )
    args = ap.parse_args(argv)

    conn = open_db(args.db)
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = _universe_for(conn, args.cadence)

    selected = [i for i in INDICATORS if args.cadence is None or i.cadence == args.cadence]
    if not selected:
        print(f"no indicators match cadence={args.cadence!r}")
        return 1

    label = args.cadence or "all"
    print(f"backfill[{label}]: {len(symbols)} symbols × {len(selected)} indicators")
    started = time.time()
    results = run_all(conn, symbols, cadence=args.cadence)
    elapsed = time.time() - started

    by_ind: Counter[str] = Counter()
    total = 0
    for r in results:
        by_ind[r.indicator] += r.rows_written
        total += r.rows_written

    for ind, rows in sorted(by_ind.items()):
        print(f"  {ind:<16}  {rows:>10,} rows")
    print(f"total: {total:,} rows in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
