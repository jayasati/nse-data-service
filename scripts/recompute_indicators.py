"""
Delete the last N days of stored indicator values and recompute them — the
safe way to re-derive indicators after a math/warm-up fix (e.g. the 2026-06
TradingView-parity fix: bar-count warm-up + Supertrend (10,3)).

Why this is correct: each indicator recomputes from RAW OHLCV
(raw_bhavcopy_cm / raw_intraday_candles), never from previously-stored
indicator rows. Deleting the last N days rolls the per-symbol watermark back N
days; the next compute pulls `min_history` bars of warm-up BEFORE the deleted
range plus the range itself, computes the whole window with the CURRENT code,
and rewrites the deleted rows correctly. Old/wrong rows outside the window are
untouched but never feed the new values.

    # dry run first — show what would be deleted, touch nothing
    python scripts/recompute_indicators.py --days 10 --dry-run

    # one symbol, intraday only — fastest way to eyeball vs TradingView
    python scripts/recompute_indicators.py --days 10 --symbols ADANIGREEN --cadence intraday

    # the real thing: last 10 days, all symbols, all cadences
    python scripts/recompute_indicators.py --days 10

    # recompute + print a verification sample (recomputed vs a fresh
    # continuous pandas-ta reference over the full series)
    python scripts/recompute_indicators.py --days 10 --symbols ADANIGREEN --verify

NEVER touches raw price/candle tables — only indicator_* outputs, which are
fully regenerable. Safe to run while the scheduler is up (WAL); the live job's
next pass simply finds the rows already present.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from collections import Counter

from nse_data.indicators.compute import run_all
from nse_data.indicators.registry import INDICATORS
from nse_data.indicators.universe import all_equity_symbols, fno_plus_nifty500
from nse_data.storage.db import open_db

_SECS_PER_DAY = 86400


def _cutoff_for(conn, indicator, days: int):
    """The watermark to delete ABOVE, derived from the table's own latest key
    (so it works regardless of how stale the data is). EOD tables key on a
    'YYYY-MM-DD' string; intraday tables on an epoch int."""
    time_col = indicator.pk_cols[1]
    row = conn.execute(
        f"SELECT MAX({time_col}) FROM {indicator.table}"
    ).fetchone()
    latest = row[0] if row else None
    if latest is None:
        return None
    if isinstance(latest, str):                      # EOD: date string
        d = _dt.date.fromisoformat(latest[:10]) - _dt.timedelta(days=days)
        return d.isoformat()
    return int(latest) - days * _SECS_PER_DAY        # intraday: epoch


def _delete_recent(conn, indicator, symbols, cutoff, *, dry_run: bool) -> int:
    time_col = indicator.pk_cols[1]
    qmarks = ",".join("?" * len(symbols))
    where = f"{time_col} > ? AND symbol IN ({qmarks})"
    args = [cutoff, *symbols]
    if dry_run:
        n = conn.execute(
            f"SELECT COUNT(*) FROM {indicator.table} WHERE {where}", args
        ).fetchone()[0]
        return n
    cur = conn.execute(f"DELETE FROM {indicator.table} WHERE {where}", args)
    return cur.rowcount


def _verify_sample(conn, symbol: str) -> None:
    """Recomputed-vs-continuous parity check for the headline intraday
    indicators (RSI/Supertrend) on one symbol — the offline equivalent of
    eyeballing TradingView."""
    import pandas_ta_classic as ta

    from nse_data.indicators.intraday_ohlcv import read_intraday_5m

    df = read_intraday_5m(conn, symbol)
    if df.empty or len(df) < 300:
        print(f"  verify: not enough 5m history for {symbol}")
        return
    ref_rsi = ta.rsi(df["close"], length=14)
    ref_st = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=3.0)
    stored_rsi = {r[0]: r[1] for r in conn.execute(
        "SELECT ts, rsi_14 FROM indicator_rsi_5m WHERE symbol=? ORDER BY ts DESC LIMIT 20",
        (symbol,))}
    stored_st = {r[0]: r[1] for r in conn.execute(
        "SELECT ts, supertrend FROM indicator_supertrend_5m WHERE symbol=? ORDER BY ts DESC LIMIT 20",
        (symbol,))}
    rsi_err = max((abs(stored_rsi[ts] - ref_rsi.loc[ts])
                   for ts in stored_rsi if ts in ref_rsi.index and stored_rsi[ts] is not None),
                  default=None)
    st_err = max((abs(stored_st[ts] - ref_st.loc[ts].iloc[0])
                  for ts in stored_st if ts in ref_st.index and stored_st[ts] is not None),
                 default=None)
    print(f"  verify {symbol}: max |RSI stored−continuous| = "
          f"{rsi_err:.3f}" if rsi_err is not None else "  verify: no RSI rows")
    if st_err is not None:
        print(f"  verify {symbol}: max |Supertrend stored−continuous| = {st_err:.3f}")
    print("  (both should be ~0.00 — that IS TradingView parity)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--days", type=int, default=10,
                    help="recompute the most recent N days (default 10)")
    ap.add_argument("--cadence", choices=["eod", "intraday", "session"],
                    help="limit to one cadence (default: all)")
    ap.add_argument("--symbols", help="comma-separated; default = the cadence universe")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be deleted, change nothing")
    ap.add_argument("--verify", action="store_true",
                    help="after recompute, print a recomputed-vs-continuous parity check")
    args = ap.parse_args(argv)

    conn = open_db(args.db)
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = (fno_plus_nifty500(conn) if args.cadence == "intraday"
                   else all_equity_symbols(conn))

    selected = [i for i in INDICATORS if args.cadence is None or i.cadence == args.cadence]
    if not selected or not symbols:
        print("nothing to do (no indicators or no symbols matched)")
        return 1

    mode = "DRY RUN — " if args.dry_run else ""
    print(f"{mode}recompute last {args.days}d · {len(symbols)} symbols · "
          f"{len(selected)} indicators")

    # 1) roll the watermark back by deleting recent rows
    deleted: Counter[str] = Counter()
    for ind in selected:
        cutoff = _cutoff_for(conn, ind, args.days)
        if cutoff is None:
            continue
        deleted[ind.table] += _delete_recent(conn, ind, symbols, cutoff, dry_run=args.dry_run)
    if not args.dry_run:
        conn.commit()
    for tbl, n in sorted(deleted.items()):
        print(f"  {'would delete' if args.dry_run else 'deleted'} {n:>10,}  {tbl}")

    if args.dry_run:
        print("dry run: no recompute. Re-run without --dry-run to apply.")
        return 0

    # 2) recompute — pulls warm-up + the deleted range, writes with current code
    started = time.time()
    results = run_all(conn, symbols, cadence=args.cadence)
    by_ind: Counter[str] = Counter()
    for r in results:
        by_ind[r.indicator] += r.rows_written
    for ind, rows in sorted(by_ind.items()):
        print(f"  recomputed {rows:>10,}  {ind}")
    print(f"recompute done in {time.time() - started:.1f}s")

    if args.verify and len(symbols) <= 5:
        for s in symbols:
            _verify_sample(conn, s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
