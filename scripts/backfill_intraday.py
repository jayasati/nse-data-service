"""
Backfill historical intraday candles into raw_intraday_candles from a broker
API (Groww or Zerodha Kite). NSE's free feeds don't publish minute history.

GROWW (default — simplest, just env vars in .env):
    GROWW_API_KEY=...            # from Groww → Trading APIs
    GROWW_TOTP_SECRET=...        # TOTP secret for that API key
    # (or a daily GROWW_ACCESS_TOKEN instead of key+secret)

    python scripts/backfill_intraday.py groww-check        # verify credentials
    # backfill MINUTE only — the dashboard derives 5m/15m/etc. from it on the fly
    python scripts/backfill_intraday.py run --top 50 --interval minute --days 30
    python scripts/backfill_intraday.py run --symbols RELIANCE,TCS --interval minute --days 60

KITE (paid Connect app — api_key + api_secret in .env):
    python scripts/backfill_intraday.py login              # print login URL
    python scripts/backfill_intraday.py token <request_token>   # -> KITE_ACCESS_TOKEN
    python scripts/backfill_intraday.py run --broker kite --top 50 --interval minute --days 60

Re-runs are idempotent (INSERT OR IGNORE on (symbol, interval, ts)).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from nse_data.brokers import kite, groww, yfinance as yfin
from nse_data.storage.db import open_db

DB_PATH = "data/nse.db"
BROKERS = {"kite": kite, "groww": groww, "yfinance": yfin}


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


def cmd_login(_args) -> int:
    print("Open this URL, log in, then copy the request_token from the redirect:\n")
    print("  " + kite.login_url())
    print("\nThen run:  python scripts/backfill_intraday.py token <request_token>")
    return 0


def cmd_token(args) -> int:
    tok = kite.exchange_request_token(args.request_token)
    print("Access token obtained. Add/replace this line in your .env:\n")
    print(f"  KITE_ACCESS_TOKEN={tok}")
    return 0


def cmd_run(args) -> int:
    broker = BROKERS[args.broker]
    if not broker.credentials_present():
        print(f"Missing {args.broker} credentials in .env.", file=sys.stderr)
        if args.broker == "kite":
            print("Run the `login` then `token` steps first.", file=sys.stderr)
        else:
            print("Set GROWW_ACCESS_TOKEN, or GROWW_API_KEY + GROWW_TOTP_SECRET.", file=sys.stderr)
        return 2

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
        try:
            candles = broker.fetch_symbol(sym, args.interval, start, end)
            n = _store(conn, sym, args.interval, candles, args.broker)
            total += n
            print(f"  [{i}/{len(symbols)}] {sym:14} {len(candles):6} candles ({n} new)")
        except Exception as e:  # keep going on per-symbol API hiccups
            errs += 1
            print(f"  [{i}/{len(symbols)}] {sym:14} ERROR — {e}")
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


def cmd_groww_check(_args) -> int:
    """Verify Groww credentials work (mints a token, fetches your profile)."""
    try:
        prof = groww.check()
        print("Groww auth OK. Profile:", prof)
        return 0
    except Exception as e:
        print(f"Groww auth FAILED: {e}", file=sys.stderr)
        return 1


def cmd_groww_token(_args) -> int:
    """Mint a Groww access token from GROWW_API_KEY + GROWW_TOTP_SECRET and print
    it (cache as GROWW_ACCESS_TOKEN in .env to skip re-minting within the day)."""
    print(f"GROWW_ACCESS_TOKEN={groww._token()}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")                 # kite: print login URL
    pt = sub.add_parser("token"); pt.add_argument("request_token")  # kite: exchange
    sub.add_parser("groww-token")           # groww: mint access token
    sub.add_parser("groww-check")           # groww: verify creds + show profile
    pp = sub.add_parser("progress")         # show backfill progress
    pp.add_argument("--interval", default="minute")
    pp.add_argument("--top", type=int, help="target count to show as denominator")
    pr = sub.add_parser("run")
    pr.add_argument("--broker", default="groww", choices=list(BROKERS.keys()),
                    help="groww (default), kite (paid Connect), or yfinance (no auth, last ~60d)")
    pr.add_argument("--symbols", help="comma-separated, e.g. RELIANCE,TCS")
    pr.add_argument("--top", type=int, help="top N by latest turnover")
    pr.add_argument("--fno", action="store_true", help="all F&O symbols")
    pr.add_argument("--interval", default="minute",
                    choices=list(kite._WINDOW_DAYS.keys()))
    pr.add_argument("--days", type=int, default=60, help="how far back from today")
    pr.add_argument("--no-resume", dest="resume", action="store_false",
                    help="re-fetch symbols even if already backfilled")
    pr.set_defaults(resume=True)

    args = p.parse_args()
    return {"login": cmd_login, "token": cmd_token,
            "groww-token": cmd_groww_token, "groww-check": cmd_groww_check,
            "progress": cmd_progress, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
