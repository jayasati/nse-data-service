"""
Full per-symbol candle backfill — fetches the COMPLETE candle set for one symbol
(1-minute, then 5-minute, then daily EOD) before moving to the next symbol.

Supports two broker backends via --source (state is tracked per source):
    angel  (default) — Angel One SmartAPI; FREE with a regular demat account,
                       multi-year 1-min history (verified back to 2021+)
    groww            — Groww v2 API; needs a PAID Trading API subscription

Built for laptop runs:
  • RESUMABLE — progress is checkpointed per (source, symbol, interval) in the
    `backfill_state` table. Ctrl-C / sleep / crash → just re-run the same
    command; finished symbols are skipped, the rest continue. Re-fetches are
    idempotent (source-priority upsert on the candle PK).
  • PRIORITY TIERS — symbols also present in raw_fno_list are tagged HIGH and
    done first; the rest are TOP500. Each symbol's tier is shown as it runs.
  • LIVE PROGRESS — per-symbol header, per-interval result line, running total
    + ETA. Plain-line output so it tees/nohups cleanly into a log file.

Storage (raw_intraday_candles, source=--source):
    1-min  -> interval='minute'    (last  --min1-days,  default 365)
    5-min  -> interval='5minute'   (tail: --min1-days .. --tail-days back)
    daily  -> interval='day'       (last  --eod-days,   default 1095)

USAGE
    # the saved top-500 universe (HIGH = F&O, done first); angel is the default source
    python scripts/backfill_groww_full.py --symbols-file data/top500_universe_2026-06-11.csv

    # ad-hoc symbols
    python scripts/backfill_groww_full.py --symbols RELIANCE,TCS

    # custom depths
    python scripts/backfill_groww_full.py --symbols-file ... \
        --min1-days 365 --tail-days 1095 --eod-days 1095

    # just SHOW progress (fetches nothing) — track an in-flight/finished run
    python scripts/backfill_groww_full.py --status --symbols-file data/top500_universe_2026-06-11.csv

    # verify credentials before a big run (angel: TOTP login; groww: subscription)
    python scripts/backfill_groww_full.py --check
    python scripts/backfill_groww_full.py --check --source groww

TRACKING A RUNNING BACKFILL
    Run it logging to a file, then watch:
        python scripts/backfill_groww_full.py --symbols-file <csv> 2>&1 | tee backfill.log
        # in another terminal:
        tail -f backfill.log
        python scripts/backfill_groww_full.py --status --symbols-file <csv>
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone

from nse_data.brokers import angel, groww
from nse_data.storage.db import open_db

DB_PATH = "data/nse.db"

# Broker backends — each exposes credentials_present(), check(), and
# fetch_symbol(symbol, interval, start, end). Pick with --source.
_BROKERS = {"groww": groww, "angel": angel}

# Source quality: NSE feeds (kite/groww/angel) outrank best-effort yfinance. A
# write only overwrites an equal-or-lower-priority row, never downgrades good data.
_SRC_PRIORITY = {"kite": 3, "groww": 3, "angel": 3, "yfinance": 1}
_PRIORITY_CASE = ("CASE raw_intraday_candles.source WHEN 'kite' THEN 3 "
                  "WHEN 'groww' THEN 3 WHEN 'angel' THEN 3 "
                  "WHEN 'yfinance' THEN 1 ELSE 0 END")


# ----------------------------------------------------------------- state table
def _ensure_state(conn) -> None:
    # `source` is in the PK so groww and angel runs track progress independently
    # against the same universe without clobbering each other.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS backfill_state (
               source       TEXT NOT NULL,
               symbol       TEXT NOT NULL,
               interval     TEXT NOT NULL,
               tier         TEXT,
               start_ts     INTEGER,
               end_ts       INTEGER,
               candles      INTEGER DEFAULT 0,
               status       TEXT DEFAULT 'pending',   -- pending|done|error
               error        TEXT,
               updated_at   INTEGER,
               PRIMARY KEY (source, symbol, interval)
           )""")
    conn.commit()


def _state_get(conn, source, symbol, interval):
    return conn.execute(
        "SELECT status, candles FROM backfill_state "
        "WHERE source=? AND symbol=? AND interval=?",
        (source, symbol, interval)).fetchone()


def _state_set(conn, source, symbol, interval, tier, start_ts, end_ts, candles, status, error=None):
    conn.execute(
        """INSERT INTO backfill_state
             (source, symbol, interval, tier, start_ts, end_ts, candles, status, error, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source, symbol, interval) DO UPDATE SET
             tier=excluded.tier, start_ts=excluded.start_ts, end_ts=excluded.end_ts,
             candles=excluded.candles, status=excluded.status, error=excluded.error,
             updated_at=excluded.updated_at""",
        (source, symbol, interval, tier, start_ts, end_ts, candles, status, error, int(time.time())))
    conn.commit()


# ----------------------------------------------------------------- candle store
def _store(conn, symbol, interval, candles, source="groww") -> int:
    """Source-priority upsert into raw_intraday_candles. Idempotent."""
    if not candles:
        return 0
    pr = _SRC_PRIORITY.get(source, 0)
    conn.executemany(
        f"""INSERT INTO raw_intraday_candles
              (symbol, interval, ts, open, high, low, close, volume, source)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol, interval, ts) DO UPDATE SET
              open=excluded.open, high=excluded.high, low=excluded.low,
              close=excluded.close, volume=excluded.volume, source=excluded.source
            WHERE {pr} >= ({_PRIORITY_CASE})""",
        [(symbol, interval, c["ts"], c["open"], c["high"], c["low"],
          c["close"], c["volume"], source) for c in candles])
    conn.commit()
    return len(candles)


# ----------------------------------------------------------------- universe
def _load_symbols(args) -> list[str]:
    if args.symbols_file:
        with open(args.symbols_file) as f:
            syms = [ln.strip().upper() for ln in f if ln.strip()]
    elif args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.fno:
        syms = []  # filled from raw_fno_list below
    else:
        raise SystemExit("specify --symbols-file, --symbols, or --fno")
    # de-dupe, preserve order
    seen, out = set(), []
    for s in syms:
        if s not in seen:
            seen.add(s); out.append(s)
    return out


def _fno_set(conn) -> set[str]:
    try:
        return {r[0] for r in conn.execute("SELECT symbol FROM raw_fno_list").fetchall()}
    except Exception:
        return set()


def _ordered_universe(conn, args):
    """Return [(symbol, tier)] with HIGH (F&O) first, then TOP500, file order kept."""
    fno = _fno_set(conn)
    syms = _load_symbols(args)
    if args.fno and not syms:
        syms = sorted(fno)
    high = [(s, "HIGH") for s in syms if s in fno]
    rest = [(s, "TOP500") for s in syms if s not in fno]
    return high + rest


# ----------------------------------------------------------------- plan
def _plan(args):
    """[(interval, start_date, end_date)] for one symbol, in fetch order.

    Windows end at --end-date (default: yesterday, the last COMPLETED day) so a
    run never stores today's partial session."""
    end = (date.fromisoformat(args.end_date) if args.end_date
           else date.today() - timedelta(days=1))
    plan = [("minute", end - timedelta(days=args.min1_days), end)]
    if args.tail_days > args.min1_days:                       # 5-min tail only
        plan.append(("5minute",
                     end - timedelta(days=args.tail_days),
                     end - timedelta(days=args.min1_days)))
    if args.eod_days > 0:
        plan.append(("day", end - timedelta(days=args.eod_days), end))
    return plan


def _epoch(d: date) -> int:
    return int(time.mktime(d.timetuple()))


def _fmt_secs(s: float) -> str:
    s = int(s)
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _span(candles):
    if not candles:
        return "—"
    f = datetime.fromtimestamp(candles[0]["ts"], timezone.utc).date()
    l = datetime.fromtimestamp(candles[-1]["ts"], timezone.utc).date()
    return f"{f}→{l}"


# ----------------------------------------------------------------- commands
def cmd_check(args) -> int:
    broker = _BROKERS[args.source]
    if not broker.credentials_present():
        print(f"{args.source} credentials missing in .env.", file=sys.stderr)
        return 2
    try:
        print(f"{args.source} auth OK:", broker.check())
        return 0
    except Exception as e:
        print(f"{args.source} auth FAILED: {e}", file=sys.stderr)
        return 1


def cmd_status(args) -> int:
    conn = open_db(args.db)
    _ensure_state(conn)
    universe = _ordered_universe(conn, args)
    intervals = [iv for iv, _, _ in _plan(args)]
    n = len(universe)
    done_syms = 0
    per_iv = {iv: 0 for iv in intervals}
    errs = []
    for sym, _tier in universe:
        sym_done = True
        for iv in intervals:
            row = _state_get(conn, args.source, sym, iv)
            st = row[0] if row else "pending"
            if st == "done":
                per_iv[iv] += 1
            elif st == "error":
                errs.append((sym, iv))
                sym_done = False
            else:
                sym_done = False
        if sym_done:
            done_syms += 1
    total_candles = conn.execute(
        "SELECT COUNT(*) FROM raw_intraday_candles WHERE source=?",
        (args.source,)).fetchone()[0]
    conn.close()
    print(f"source: {args.source} · universe: {n} symbols · intervals: {', '.join(intervals)}")
    print(f"symbols fully done: {done_syms}/{n} ({100*done_syms/max(n,1):.1f}%)")
    for iv in intervals:
        print(f"  {iv:8} done: {per_iv[iv]}/{n}")
    print(f"{args.source} candles stored: {total_candles:,}")
    if errs:
        print(f"errors: {len(errs)} (e.g. {errs[:3]}) — run --check to verify credentials")
    return 0


def cmd_run(args) -> int:
    broker = _BROKERS[args.source]
    if not broker.credentials_present():
        print(f"{args.source} credentials missing in .env.", file=sys.stderr)
        return 2

    conn = open_db(args.db)
    _ensure_state(conn)
    universe = _ordered_universe(conn, args)
    plan = _plan(args)
    n = len(universe)
    n_high = sum(1 for _, t in universe if t == "HIGH")

    print("=" * 64)
    print(f"{args.source} full backfill · {n} symbols ({n_high} HIGH, {n - n_high} TOP500)")
    pl_desc = " · ".join(
        f"{iv}:{s}→{e}" for iv, s, e in plan)
    print(pl_desc)
    print("resumable — re-run the same command to continue; finished symbols skip")
    print("=" * 64)

    run_start = time.time()
    processed = 0          # symbols touched this run (for ETA)
    grand_candles = 0
    try:
        for i, (sym, tier) in enumerate(universe, 1):
            # already fully done?
            if all((_state_get(conn, args.source, sym, iv) or ("pending",))[0] == "done"
                   for iv, _, _ in plan):
                print(f"[{i:4}/{n}] {sym:14} {tier:6}  skip (already complete)")
                continue

            print(f"[{i:4}/{n}] {sym:14} {tier}")
            sym_start = time.time()
            sym_candles = 0
            for iv, sd, ed in plan:
                row = _state_get(conn, args.source, sym, iv)
                if row and row[0] == "done":
                    print(f"    {iv:8} ↻ already done ({row[1]:,})")
                    continue
                t0 = time.time()
                try:
                    candles = broker.fetch_symbol(sym, iv, sd, ed)
                    stored = _store(conn, sym, iv, candles, args.source)
                    _state_set(conn, args.source, sym, iv, tier, _epoch(sd), _epoch(ed),
                               stored, "done")
                    sym_candles += stored
                    print(f"    {iv:8} ✓ {stored:>8,} candles  {_span(candles):23} "
                          f"({time.time()-t0:.1f}s)")
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    _state_set(conn, args.source, sym, iv, tier, _epoch(sd), _epoch(ed),
                               0, "error", str(e)[:200])
                    print(f"    {iv:8} ✗ ERROR — {e}")
            grand_candles += sym_candles
            processed += 1
            # ETA from symbols processed this run
            elapsed = time.time() - run_start
            rate = elapsed / processed
            eta = _fmt_secs(rate * (n - i))
            print(f"  done · {sym_candles:,} candles · {time.time()-sym_start:.1f}s "
                  f"· {i}/{n} ({100*i/n:.1f}%) · elapsed {_fmt_secs(elapsed)} · ETA ~{eta}")
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\ninterrupted — progress saved; re-run the same command to resume.")
    finally:
        conn.close()

    print("=" * 64)
    print(f"this run: {processed} symbols processed · {grand_candles:,} candles · "
          f"{_fmt_secs(time.time()-run_start)}")
    print("run with --status to see overall completion.")
    return 0


def cmd_day(args) -> int:
    """Top-up: fetch 1-min candles for ONE day for every symbol in the universe.

    Ignores the full-backfill done-state (those symbols would otherwise skip) but
    is itself resumable — progress is tracked under interval 'minute@<day>'.
    Idempotent upsert, so partial pre-existing candles for the day (e.g. a feed
    that stopped at 14:50) are overwritten/completed, never duplicated."""
    day = date.fromisoformat(args.day)
    broker = _BROKERS[args.source]
    if not broker.credentials_present():
        print(f"{args.source} credentials missing in .env.", file=sys.stderr)
        return 2

    conn = open_db(args.db)
    _ensure_state(conn)
    universe = _ordered_universe(conn, args)
    n = len(universe)
    state_iv = f"minute@{day}"

    print("=" * 64)
    print(f"{args.source} single-day top-up · {n} symbols · minute candles for {day}")
    print("resumable — re-run the same command to continue; finished symbols skip")
    print("=" * 64)

    run_start = time.time()
    processed = 0
    grand_candles = 0
    try:
        for i, (sym, tier) in enumerate(universe, 1):
            row = _state_get(conn, args.source, sym, state_iv)
            if row and row[0] == "done":
                print(f"[{i:4}/{n}] {sym:14} {tier:6}  skip (already done, {row[1]:,})")
                continue
            t0 = time.time()
            try:
                candles = broker.fetch_symbol(sym, "minute", day, day)
                stored = _store(conn, sym, "minute", candles, args.source)
                _state_set(conn, args.source, sym, state_iv, tier,
                           _epoch(day), _epoch(day), stored, "done")
                grand_candles += stored
                print(f"[{i:4}/{n}] {sym:14} {tier:6}  ✓ {stored:>6,} candles "
                      f"{_span(candles):23} ({time.time()-t0:.1f}s)")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                _state_set(conn, args.source, sym, state_iv, tier,
                           _epoch(day), _epoch(day), 0, "error", str(e)[:200])
                print(f"[{i:4}/{n}] {sym:14} {tier:6}  ✗ ERROR — {e}")
            processed += 1
            if processed % 25 == 0:
                elapsed = time.time() - run_start
                eta = _fmt_secs(elapsed / processed * (n - i))
                print(f"  · {grand_candles:,} candles · elapsed {_fmt_secs(elapsed)} · ETA ~{eta}")
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\ninterrupted — progress saved; re-run the same command to resume.")
    finally:
        conn.close()

    print("=" * 64)
    print(f"this run: {processed} symbols · {grand_candles:,} candles · "
          f"{_fmt_secs(time.time()-run_start)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=sorted(_BROKERS), default="angel",
                   help="broker backend to fetch from (default angel)")
    p.add_argument("--symbols-file", help="one symbol per line (e.g. the top-500 CSV)")
    p.add_argument("--symbols", help="comma-separated, e.g. RELIANCE,TCS")
    p.add_argument("--fno", action="store_true", help="use all raw_fno_list symbols")
    p.add_argument("--min1-days", type=int, default=365, help="1-min depth (default 365)")
    p.add_argument("--tail-days", type=int, default=1095,
                   help="5-min tail goes back this far (default 1095); the tail is "
                        "[--tail-days .. --min1-days] so it doesn't overlap the 1-min year")
    p.add_argument("--eod-days", type=int, default=1095, help="daily depth (default 1095)")
    p.add_argument("--end-date",
                   help="last day to fetch, YYYY-MM-DD (default: yesterday — "
                        "the last completed session; today's partial day is never fetched)")
    p.add_argument("--day",
                   help="top-up mode: fetch 1-min candles for just this one day "
                        "(YYYY-MM-DD) for the whole universe; run AFTER market close")
    p.add_argument("--db", default=DB_PATH)
    p.add_argument("--status", action="store_true", help="show progress, fetch nothing")
    p.add_argument("--check", action="store_true", help="verify Groww auth/subscription")
    args = p.parse_args()

    if args.check:
        return cmd_check(args)
    if args.status:
        return cmd_status(args)
    if args.day:
        return cmd_day(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
