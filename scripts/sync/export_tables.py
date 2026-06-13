"""Export collected/derived tables into a transfer DB (run ON the server).

Stdlib-only — scp this single file to the server and run it with the system
python3; no repo install or venv needed.

    python3 export_tables.py --src data/nse.db --out data/server_export.db
    python3 export_tables.py ... --tables raw_announcements,signals   # override

The default list is the "what the always-on box collected while the laptop
slept" set: raw collector tables + parsed/derived layers. Deliberately
excluded (huge, regenerable, or machine-local): raw_equity_quotes,
raw_option_chain, indicator_* time series, paper_trades, schema_migrations —
add them via --tables if you really want them.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time

DEFAULT_TABLES: tuple[str, ...] = (
    # Layer 1/2 — collected raw
    "raw_announcements", "raw_financial_results", "raw_board_meetings",
    "raw_bhavcopy_cm", "raw_price_bands", "raw_high_low_52w", "raw_oi_spurts",
    "raw_gift_nifty", "raw_india_vix", "raw_macro", "raw_nsdl_fpi",
    "raw_shareholding_pattern",
    # Layer 3 — parsed
    "extracted_financials", "raw_rating_actions", "raw_rating_lines",
    "raw_analyst_ratings",
    # Events / fundamentals
    "consensus_estimates", "pending_events", "earnings_setups", "macro_rates",
    "stock_fundamentals", "delivery_conviction", "stock_profile_daily",
    # Market context + levels/patterns
    "market_state", "sector_state", "indicator_levels", "patterns",
    # Signal engine archive (id-remapped on import)
    "signals", "signal_features", "signal_outcomes",
)


def export(src: str, out: str, tables: list[str]) -> int:
    conn = sqlite3.connect(out)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute(f"ATTACH DATABASE 'file:{src}?mode=ro' AS src")
    have = {r[0] for r in conn.execute(
        "SELECT name FROM src.sqlite_master WHERE type='table'")}

    started = time.time()
    total = 0
    for t in tables:
        if t not in have:
            print(f"  -- {t}: not on this machine, skipped")
            continue
        conn.execute(f'CREATE TABLE "{t}" AS SELECT * FROM src."{t}"')
        n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        total += n
        print(f"  {t}: {n:,} rows")
        conn.commit()
    conn.execute("DETACH DATABASE src")
    conn.close()
    print(f"done: {total:,} rows in {time.time() - started:,.0f}s -> {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/nse.db")
    ap.add_argument("--out", default="data/server_export.db")
    ap.add_argument("--tables", help="comma-separated override of the default list")
    args = ap.parse_args()
    tables = ([s.strip() for s in args.tables.split(",")] if args.tables
              else list(DEFAULT_TABLES))
    return export(args.src, args.out, tables)


if __name__ == "__main__":
    sys.exit(main())
