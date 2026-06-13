"""Import a candles_export.db into this machine's nse.db — delete-then-load.

Stdlib-only (no repo install needed; scp this file alone and run it with the
system python3). For every symbol in the export's manifest:

    1. DELETE all existing raw_intraday_candles rows for that symbol
       (the clean-slate reload the sync is for — old broker data goes away)
    2. INSERT the exported rows
    3. verify the loaded count matches the manifest

One transaction per symbol, so a crash mid-run leaves whole symbols either
old or new (re-running is safe — it just redoes the delete+load). Run with
the collector STOPPED to avoid long writer-lock contention.

    python3 scripts/sync/import_candles.py \
        --db data/nse.db --export data/candles_export.db [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time


def import_candles(db: str, export: str, dry_run: bool) -> int:
    conn = sqlite3.connect(db, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"ATTACH DATABASE 'file:{export}?mode=ro' AS exp")

    manifest = conn.execute(
        "SELECT symbol, SUM(rows) FROM exp.sync_manifest GROUP BY symbol ORDER BY symbol"
    ).fetchall()
    exp_total = sum(r[1] for r in manifest)
    print(f"export holds {exp_total:,} rows across {len(manifest)} symbols")

    existing = dict(conn.execute(
        "SELECT symbol, COUNT(*) FROM raw_intraday_candles "
        "WHERE symbol IN (SELECT DISTINCT symbol FROM exp.sync_manifest) "
        "GROUP BY symbol"))
    print(f"target currently holds {sum(existing.values()):,} rows for these symbols "
          f"({len(existing)} symbols present) — these will be DELETED")
    if dry_run:
        print("--dry-run: nothing changed")
        return 0

    started = time.time()
    loaded = mismatches = 0
    for i, (symbol, exp_rows) in enumerate(manifest, 1):
        conn.execute("BEGIN")
        conn.execute("DELETE FROM raw_intraday_candles WHERE symbol = ?", (symbol,))
        conn.execute(
            "INSERT OR REPLACE INTO raw_intraday_candles "
            "(symbol, interval, ts, open, high, low, close, volume) "
            "SELECT symbol, interval, ts, open, high, low, close, volume "
            "FROM exp.raw_intraday_candles WHERE symbol = ?", (symbol,))
        conn.commit()
        got = conn.execute(
            "SELECT COUNT(*) FROM raw_intraday_candles WHERE symbol = ?", (symbol,)
        ).fetchone()[0]
        loaded += got
        if got != exp_rows:
            mismatches += 1
            print(f"  !! {symbol}: loaded {got:,} != manifest {exp_rows:,}")
        if i % 50 == 0 or i == len(manifest):
            rate = loaded / max(time.time() - started, 1e-9)
            print(f"  {i}/{len(manifest)} symbols, {loaded:,} rows ({rate:,.0f} rows/s)")

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    print(f"done: {loaded:,} rows in {time.time() - started:,.0f}s, "
          f"{mismatches} count mismatches")
    return 1 if mismatches else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--export", default="data/candles_export.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return import_candles(args.db, args.export, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
