"""
Compute every registered indicator across the symbol universe.

    python scripts/backfill_indicators.py                 # all symbols in bhavcopy
    python scripts/backfill_indicators.py --symbols RELIANCE,TCS
    python scripts/backfill_indicators.py --db data/nse.db

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
from nse_data.storage.db import open_db


def _all_symbols(conn) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM raw_bhavcopy_cm WHERE series = 'EQ' "
        "ORDER BY symbol"
    ).fetchall()
    return [r[0] for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument(
        "--symbols",
        help="Comma-separated symbols (default: every EQ symbol in raw_bhavcopy_cm)",
    )
    args = ap.parse_args(argv)

    conn = open_db(args.db)
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else _all_symbols(conn)
    )

    print(f"backfill: {len(symbols)} symbols × {len(INDICATORS)} indicators")
    started = time.time()
    results = run_all(conn, symbols)
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
