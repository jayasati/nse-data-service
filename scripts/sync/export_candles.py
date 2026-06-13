"""Export this machine's raw_intraday_candles into a compact transfer DB.

Stdlib-only (runs anywhere with python3, no repo install needed).

    python3 scripts/sync/export_candles.py \
        --src data/nse.db --out data/candles_export.db [--symbols A,B,...]

Produces a SQLite file containing:
  * raw_intraday_candles — every row for the exported symbols (all intervals)
  * sync_manifest        — (symbol, interval, rows, min_ts, max_ts) for the
                           importer to verify against after loading

No indexes are written (the import target already has them; keeping the
export flat makes the file smaller and the insert faster).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time


def export(src: str, out: str, symbols: list[str] | None) -> int:
    conn = sqlite3.connect(out)
    conn.execute("PRAGMA journal_mode=OFF")        # transfer file: no durability needed
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute(f"ATTACH DATABASE 'file:{src}?mode=ro' AS src")

    if symbols is None:
        symbols = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM src.raw_intraday_candles ORDER BY symbol")]
    print(f"exporting {len(symbols)} symbols from {src} -> {out}")

    conn.execute("""
        CREATE TABLE raw_intraday_candles (
            symbol TEXT NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER
        )""")
    conn.execute("""
        CREATE TABLE sync_manifest (
            symbol TEXT NOT NULL, interval TEXT NOT NULL,
            rows INTEGER NOT NULL, min_ts INTEGER, max_ts INTEGER,
            PRIMARY KEY (symbol, interval)
        )""")

    started = time.time()
    total = 0
    for i, symbol in enumerate(symbols, 1):
        cur = conn.execute(
            "INSERT INTO raw_intraday_candles "
            "SELECT symbol, interval, ts, open, high, low, close, volume "
            "FROM src.raw_intraday_candles WHERE symbol = ?", (symbol,))
        total += cur.rowcount
        conn.execute(
            "INSERT INTO sync_manifest "
            "SELECT symbol, interval, COUNT(*), MIN(ts), MAX(ts) "
            "FROM src.raw_intraday_candles WHERE symbol = ? GROUP BY interval",
            (symbol,))
        if i % 50 == 0 or i == len(symbols):
            conn.commit()
            rate = total / max(time.time() - started, 1e-9)
            print(f"  {i}/{len(symbols)} symbols, {total:,} rows ({rate:,.0f} rows/s)")
    conn.commit()
    conn.execute("DETACH DATABASE src")
    conn.close()
    print(f"done: {total:,} rows in {time.time() - started:,.0f}s")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/nse.db")
    ap.add_argument("--out", default="data/candles_export.db")
    ap.add_argument("--symbols", help="comma-separated; default = all with candles")
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    return export(args.src, args.out, symbols)


if __name__ == "__main__":
    sys.exit(main())
