"""
Backfill historical intraday candles into raw_intraday_candles. NSE's free feeds
don't publish minute history, so we pull from a broker API.

ANGEL ONE (PRIMARY — free with any demat account, daily TOTP login):
    ANGEL_API_KEY / ANGEL_CLIENT_CODE / ANGEL_PIN / ANGEL_TOTP_SECRET in .env

    # backfill MINUTE only — the dashboard derives 5m/15m/etc. from it on the fly
    python scripts/backfill_intraday.py run --top 500 --interval minute --days 5
    python scripts/backfill_intraday.py run --symbols RELIANCE,TCS --interval minute --days 60

YAHOO FINANCE (SECONDARY — no auth, last ~60d, off by ₹1-2; automatic per-symbol
fallback when Angel fails, or force with --broker yfinance).

Re-runs are idempotent (INSERT OR IGNORE on (symbol, interval, ts)).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from nse_data.brokers import angel, yfinance as yfin
from nse_data.storage.db import open_db

DB_PATH = "data/nse.db"
BROKERS = {"angel": angel, "yfinance": yfin}   # angel = primary, yfinance = secondary


def _universe(conn, args) -> list[str]:
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.fno:
        rows = conn.execute("SELECT symbol FROM raw_fno_list ORDER BY symbol").fetchall()
        return [r[0] for r in rows]
    if args.top:
        d = conn.execute("SELECT MAX(date) FROM raw_bhavcopy_cm").fetchone()[0]
        rows = conn.execute(
            "SELECT symbol FROM raw_bhavcopy_cm WHERE date=? AND series='EQ' "
            "ORDER BY turnover_lacs DESC LIMIT ?", (d, args.top),
        ).fetchall()
        return [r[0] for r in rows]
    raise SystemExit("specify --symbols, --top N, or --fno")


def _store(conn, symbol: str, interval: str, candles: list[dict], source: str) -> int:
    if not candles:
        return 0
    cur = conn.cursor()
    cur.executemany(
        "INSERT OR IGNORE INTO raw_intraday_candles "
        "(symbol, interval, ts, open, high, low, close, volume, source) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(symbol, interval, c["ts"], c["open"], c["high"], c["low"],
          c["close"], c["volume"], source) for c in candles],
    )
    conn.commit()
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(candles)


def cmd_run(args) -> int:
    broker = BROKERS[args.broker]
    if not broker.credentials_present():
        print(f"Missing {args.broker} credentials in .env "
              f"(angel: ANGEL_API_KEY/CLIENT_CODE/PIN/TOTP_SECRET).", file=sys.stderr)
        return 2
    # secondary fallback: Yahoo (no auth) for symbols the primary can't serve.
    secondary = yfin if broker is not yfin and yfin.credentials_present() else None

    conn = open_db(DB_PATH)
    symbols = _universe(conn, args)
    end = date.today()
    start = end - timedelta(days=args.days)
    import time as _t
    start_floor = _t.mktime(start.timetuple())  # epoch of target start (local≈IST)
    print(f"backfilling {len(symbols)} symbols · {args.broker} · "
          f"interval={args.interval} · {start} → {end}"
          + (" · resume on" if args.resume else ""))

    total = done = errs = 0
    for i, sym in enumerate(symbols, 1):
        # Resume: skip symbols whose stored history already reaches ~the target
        # start (within a week). Re-runs thus continue where they left off.
        if args.resume:
            row = conn.execute(
                "SELECT COUNT(*), MIN(ts) FROM raw_intraday_candles "
                "WHERE symbol=? AND interval=?", (sym, args.interval)).fetchone()
            if row and row[0] and row[1] is not None and row[1] <= start_floor + 7 * 86400:
                done += 1
                print(f"  [{i}/{len(symbols)}] {sym:14} skip (have {row[0]})")
                continue
        candles, src = None, args.broker
        try:
            candles = broker.fetch_symbol(sym, args.interval, start, end)
        except Exception as e:  # primary failed → try the secondary (Yahoo)
            if secondary:
                try:
                    candles = secondary.fetch_symbol(sym, args.interval, start, end)
                    src = "yfinance"
                    print(f"  [{i}/{len(symbols)}] {sym:14} primary failed ({e}); yfinance fallback")
                except Exception as e2:
                    errs += 1
                    print(f"  [{i}/{len(symbols)}] {sym:14} ERROR (both) — {e2}")
                    continue
            else:
                errs += 1
                print(f"  [{i}/{len(symbols)}] {sym:14} ERROR — {e}")
                continue
        n = _store(conn, sym, args.interval, candles or [], src)
        total += n
        tag = "" if src == args.broker else f" via {src}"
        print(f"  [{i}/{len(symbols)}] {sym:14} {len(candles or []):6} candles ({n} new){tag}")
    conn.close()
    print(f"done · {total} candles stored · {done} skipped · {errs} errors")
    return 0


def cmd_progress(args) -> int:
    """Print backfill progress: symbols done, candle count, DB span."""
    conn = open_db(DB_PATH)
    interval = args.interval or "minute"
    syms, cnt, mn, mx = conn.execute(
        "SELECT COUNT(DISTINCT symbol), COUNT(*), MIN(ts), MAX(ts) "
        "FROM raw_intraday_candles WHERE interval=?", (interval,)).fetchone()
    conn.close()
    import datetime
    span = ""
    if mn and mx:
        f = datetime.datetime.fromtimestamp(mn).date()
        l = datetime.datetime.fromtimestamp(mx).date()
        span = f" · {f} → {l}"
    target = f" / {args.top}" if args.top else ""
    print(f"{interval}: {syms}{target} symbols · {cnt:,} candles"
          f" · ~{cnt*46/1e9:.2f} GB{span}")
    return 0


def cmd_angel_check(_args) -> int:
    """Verify Angel credentials work (TOTP login + profile)."""
    try:
        print("Angel auth OK. Profile:", angel.check())
        return 0
    except Exception as e:
        print(f"Angel auth FAILED: {e}", file=sys.stderr)
        return 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("angel-check")           # angel: verify creds + show profile
    pp = sub.add_parser("progress")         # show backfill progress
    pp.add_argument("--interval", default="minute")
    pp.add_argument("--top", type=int, help="target count to show as denominator")
    pr = sub.add_parser("run")
    pr.add_argument("--broker", default="angel", choices=list(BROKERS.keys()),
                    help="angel (default/primary), or yfinance (no auth, last ~60d). "
                         "Angel auto-falls-back to yfinance per symbol on failure.")
    pr.add_argument("--symbols", help="comma-separated, e.g. RELIANCE,TCS")
    pr.add_argument("--top", type=int, help="top N by latest turnover")
    pr.add_argument("--fno", action="store_true", help="all F&O symbols")
    pr.add_argument("--interval", default="minute",
                    choices=list(angel._WINDOW_DAYS.keys()))
    pr.add_argument("--days", type=int, default=60, help="how far back from today")
    pr.add_argument("--no-resume", dest="resume", action="store_false",
                    help="re-fetch symbols even if already backfilled")
    pr.set_defaults(resume=True)

    args = p.parse_args()
    return {"angel-check": cmd_angel_check,
            "progress": cmd_progress, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
