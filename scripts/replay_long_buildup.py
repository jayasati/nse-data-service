"""
Replay the live `long_buildup` rule over historical OI snapshots (a backtest).

    python scripts/replay_long_buildup.py
    python scripts/replay_long_buildup.py --oi 3.0 --price 1.0 --vol 1.5

WHY THIS IS A SCRIPT, NOT A BACKTESTER STRATEGY
-----------------------------------------------
The OHLCV backtester (strategies/*) drives off price bars. `long_buildup`
depends on point-in-time OPEN INTEREST (raw_oi_spurts), which isn't derivable
from price bars — so it can't be a bar engine. Instead we replay the exact live
rule (signals/compute.py + the detect.py thresholds) over every stored OI
snapshot, then score each fire by its next-day forward return from bhavcopy.

DATA CAVEAT (read before trusting the numbers)
----------------------------------------------
raw_oi_spurts only has a short history (the OI feed is recent — see the date
range this script prints). The sample is therefore tiny and NOT statistically
meaningful yet; treat the output as a wiring/sanity check, not an edge estimate.
Re-run after several more weeks of live OI collection for a real read.

Read-only: computes and prints, writes nothing to the DB.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime

from nse_data.scheduler.market_hours import IST
from nse_data.signals import compute
from nse_data.storage.db import open_db


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="data/nse.db")
    p.add_argument("--oi", type=float, default=3.0, help="min oi_change_pct")
    p.add_argument("--price", type=float, default=1.0, help="min price_change_pct")
    p.add_argument("--vol", type=float, default=1.5, help="min volume_ratio")
    p.add_argument("--skip-vol", action="store_true",
                   help="ignore the volume leg (intraday may be missing for some names)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    conn = open_db(args.db)

    span = conn.execute(
        "SELECT MIN(as_of), MAX(as_of), COUNT(DISTINCT as_of) FROM raw_oi_spurts"
    ).fetchone()
    if not span or span[0] is None:
        print("raw_oi_spurts is empty — nothing to replay.")
        return 1
    first = datetime.fromtimestamp(span[0], IST).date()
    last = datetime.fromtimestamp(span[1], IST).date()
    print(f"OI history: {first} → {last}  ({span[2]} snapshots)\n")

    fires = _replay(conn, args)
    _report(fires, args, first, last)
    conn.close()
    return 0


def _replay(conn, args) -> list[dict]:
    """One signal per (symbol, day): the first snapshot that meets the rule."""
    rows = conn.execute(
        "SELECT symbol, as_of, latest_oi, prev_oi FROM raw_oi_spurts "
        "ORDER BY as_of ASC"
    ).fetchall()

    seen: set[tuple[str, str]] = set()      # (symbol, day) already fired
    fires: list[dict] = []

    for symbol, as_of, latest_oi, prev_oi in rows:
        now = datetime.fromtimestamp(as_of, IST)
        day = now.date().isoformat()
        if (symbol, day) in seen:
            continue

        if not prev_oi:
            continue
        oi_change = (latest_oi - prev_oi) / prev_oi * 100.0
        if oi_change < args.oi:
            continue

        price_change, price = _price_at(conn, symbol, as_of)
        if price_change is None or price is None or price_change < args.price:
            continue

        if not args.skip_vol:
            vol = compute.compute_volume_ratio(conn, symbol, now=now)
            if vol is None or vol < args.vol:
                continue
        else:
            vol = None

        fwd = _next_day_return(conn, symbol, day, price)
        seen.add((symbol, day))
        fires.append({
            "symbol": symbol, "day": day, "oi": oi_change,
            "price_change": price_change, "vol": vol, "entry": price, "fwd_1d": fwd,
        })
    return fires


def _price_at(conn, symbol: str, as_of: int):
    """(pct_change, last_price) from the latest quote at or before `as_of`."""
    row = conn.execute(
        "SELECT pct_change, last_price FROM raw_equity_quotes "
        "WHERE symbol = ? AND as_of <= ? ORDER BY as_of DESC LIMIT 1",
        (symbol, as_of),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _next_day_return(conn, symbol: str, day: str, entry: float):
    """Next trading day's bhavcopy close vs the entry price, in percent."""
    row = conn.execute(
        "SELECT close FROM raw_bhavcopy_cm WHERE symbol = ? AND series = 'EQ' "
        "AND date > ? AND close IS NOT NULL ORDER BY date ASC LIMIT 1",
        (symbol, day),
    ).fetchone()
    if not row or not entry:
        return None
    return (row[0] / entry - 1.0) * 100.0


def _report(fires: list[dict], args, first, last) -> None:
    scored = [f for f in fires if f["fwd_1d"] is not None]
    wins = sum(1 for f in scored if f["fwd_1d"] > 0)
    losses = sum(1 for f in scored if f["fwd_1d"] < 0)
    decided = wins + losses
    avg = sum(f["fwd_1d"] for f in scored) / len(scored) if scored else 0.0

    print("=" * 60)
    print("  Replay — long_buildup")
    print(f"  Rule       : oi≥{args.oi}  price≥{args.price}  "
          f"vol≥{args.vol}{' (skipped)' if args.skip_vol else ''}")
    print(f"  Window     : {first} → {last}")
    print("-" * 60)
    print(f"  Signals    : {len(fires)}  ({len(scored)} with a next-day price)")
    print(f"  Win rate   : {wins}/{decided} = "
          f"{(wins/decided*100 if decided else 0):.1f}%   (next-day fwd return)")
    print(f"  Avg fwd 1d : {avg:+.2f}%")
    print("=" * 60)

    if not fires:
        print("\nNo signals — try --skip-vol or looser thresholds on this small sample.")
        return

    top = sorted(scored, key=lambda f: (f["fwd_1d"] or 0), reverse=True)[:10]
    print("\nSample fires (top by next-day return):")
    for f in top:
        vol = f"{f['vol']:.1f}×" if f["vol"] is not None else "—"
        print(f"  {f['day']}  {f['symbol']:14} oi {f['oi']:5.1f}%  "
              f"px {f['price_change']:+5.2f}%  vol {vol:>6}  fwd1d {f['fwd_1d']:+6.2f}%")

    by_day = Counter(f["day"] for f in fires)
    print("\nSignals per day:", dict(sorted(by_day.items())))
    print("\nNOTE: tiny sample — sanity check only, not an edge estimate.")


if __name__ == "__main__":
    raise SystemExit(main())
