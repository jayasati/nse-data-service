#!/usr/bin/env python
"""Context-aware intraday momentum backtest (point-in-time, net-of-cost).

Hypothesis under test (user, 2026-06-26): the prior intraday backtests were net-negative
because they traded mechanical indicator rules (ORB/VWAP) on raw price = noise. A
*context-aware* entry — confirmed momentum AND a real catalyst / sector-basket moving
together — should sustain to EOD and be tradeable.

Design (NO LOOKAHEAD — only data knowable at entry time is used to FILTER):
  Universe : tradeable_universe grade A/B (liquid enough for intraday + leverage).
  Trigger  : an up-move event that day (intraday_move_events, move_pct >= MIN_MOVE).
  Entry    : first minute bar at/after 09:30 IST whose HIGH crosses open*(1+ENTRY_T);
             fill at the NEXT bar's open (no intrabar peeking). Skip if not crossed by 14:00.
  Context  : measured AT entry time only —
               sector_breadth = # of NSE-sectoral-index peers already up >BREADTH_T from
                                their own open by entry time (the "basket is moving" signal)
               catalyst       = any raw_news / raw_announcements stamped that day
  Exit     : (A) EOD square-off (last bar close)
             (B) chandelier trailing stop TRAIL% off the high-since-entry, else EOD
  P&L      : net-of-cost via costs.model.compute_costs (segment='intraday'), notional NOTIONAL.
             Leverage is NOT applied to % — it scales both win and loss equally; we report the
             unleveraged net% expectancy (the only thing that decides if leverage is +EV).

Output: expectancy by context bucket. The test is whether requiring breadth/catalyst lifts a
flat/negative unfiltered expectancy into clearly-positive territory.
"""
from __future__ import annotations
import argparse, sqlite3, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from nse_data.costs.model import compute_costs  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
SECTOR_INDICES = ["NIFTY AUTO", "NIFTY METAL", "NIFTY ENERGY", "NIFTY IT", "NIFTY PHARMA",
                  "NIFTY FMCG", "NIFTY BANK", "NIFTY FINANCIAL SERVICES", "NIFTY REALTY"]


def day_epoch_range(d: str) -> tuple[int, int]:
    y, m, dd = map(int, d.split("-"))
    start = int(datetime(y, m, dd, 0, 0, tzinfo=IST).timestamp())
    return start, start + 86400


def hm(ep: int) -> str:
    return datetime.fromtimestamp(ep, IST).strftime("%H:%M")


def load_day_bars(conn, syms, d):
    """{symbol: [(ts, open, high, low, close, vol), ...]} for one IST date, minute bars."""
    lo, hi = day_epoch_range(d)
    out = defaultdict(list)
    q = ("SELECT symbol, ts, open, high, low, close, volume FROM raw_intraday_candles "
         "WHERE interval='minute' AND ts>=? AND ts<? AND symbol IN (%s) ORDER BY symbol, ts"
         % ",".join("?" * len(syms)))
    for sym, ts, o, h, l, c, v in conn.execute(q, (lo, hi, *syms)):
        if o and h and l and c:
            out[sym].append((ts, o, h, l, c, v or 0))
    return out


def first_cross(bars, thresh_mult, after_ep):
    """First bar (idx) at/after after_ep whose HIGH >= open_of_day*thresh_mult. Returns (idx, ts)."""
    if not bars:
        return None
    day_open = bars[0][1]
    target = day_open * thresh_mult
    for i, (ts, o, h, l, c, v) in enumerate(bars):
        if ts >= after_ep and h >= target:
            return i, ts
    return None


def run(db, days, min_move, entry_t, breadth_t, trail, notional, market_open_min=15,
        trigger="event"):
    conn = sqlite3.connect(db, timeout=30)
    conn.execute("PRAGMA query_only=ON")

    universe = [r[0] for r in conn.execute(
        "SELECT symbol FROM tradeable_universe WHERE grade IN ('A core','B tradeable')")]
    uset = set(universe)
    # sector map: symbol -> set(indices)
    sec_of = defaultdict(set)
    for idx in SECTOR_INDICES:
        for (s,) in conn.execute("SELECT symbol FROM raw_index_members WHERE index_name=?", (idx,)):
            if s in uset:
                sec_of[s].add(idx)

    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM intraday_move_events ORDER BY date DESC LIMIT ?", (days,))]
    dates.reverse()
    print(f"universe={len(universe)} A/B names · sectors={len(SECTOR_INDICES)} · "
          f"window={len(dates)} sessions ({dates[0]}..{dates[-1]})", flush=True)

    trades = []
    for di, d in enumerate(dates):
        lo, hi = day_epoch_range(d)
        # need bars for ALL universe names (breadth) — load once per day
        bars = load_day_bars(conn, universe, d)
        if not bars:
            continue
        if trigger == "event":
            # SELECTION-BIASED: only symbol-days the move-detector post-hoc labeled (lookahead)
            ev = conn.execute(
                "SELECT symbol, move_pct, pattern FROM intraday_move_events "
                "WHERE date=? AND direction='up' AND move_pct>=?", (d, min_move)).fetchall()
            ev = [(s, mp, pat) for s, mp, pat in ev if s in uset]
        else:
            # CLEAN: trigger on ANY universe symbol — no post-hoc conditioning. pattern unknown.
            ev_pat = {s: p for s, mp, p in conn.execute(
                "SELECT symbol, move_pct, pattern FROM intraday_move_events WHERE date=?", (d,))}
            ev = [(s, None, ev_pat.get(s, "n/a")) for s in bars]
        if not ev:
            continue
        open_after = lo + market_open_min * 60  # 09:30 IST gate (15 min after 09:15)

        # precompute per-symbol time-of-first-cross +breadth_t (basket marker)
        breadth_cross = {}
        for s, b in bars.items():
            fc = first_cross(b, 1 + breadth_t / 100.0, lo)  # from open, any time
            breadth_cross[s] = fc[1] if fc else None

        # catalyst flags for this day (news OR announcement stamped within the day)
        cat = set()
        for (s,) in conn.execute(
                "SELECT DISTINCT symbol FROM raw_news WHERE published_epoch>=? AND published_epoch<?",
                (lo - 14 * 3600, hi)):  # include overnight news up to the session
            cat.add(s)
        for (s,) in conn.execute(
                "SELECT DISTINCT symbol FROM raw_announcements WHERE broadcast_epoch>=? AND broadcast_epoch<?",
                (lo - 14 * 3600, hi)):
            cat.add(s)

        for sym, move_pct, pattern in ev:
            b = bars.get(sym)
            if not b or len(b) < 30:
                continue
            day_open = b[0][1]
            fc = first_cross(b, 1 + entry_t / 100.0, open_after)
            if fc is None:
                continue
            ci, cts = fc
            if ci + 1 >= len(b):
                continue
            if cts > lo + (14 * 60 + 0) * 60:  # crossed after ~14:00 -> too late, skip
                continue
            entry_px = b[ci + 1][1]  # NEXT bar open = realistic fill, no intrabar peek
            entry_ts = b[ci + 1][0]

            # context @ entry (no lookahead)
            peers = set()
            for idx in sec_of.get(sym, ()):
                for (p,) in conn.execute("SELECT symbol FROM raw_index_members WHERE index_name=?", (idx,)):
                    if p != sym and p in uset:
                        peers.add(p)
            breadth = sum(1 for p in peers
                          if breadth_cross.get(p) is not None and breadth_cross[p] <= entry_ts)
            has_cat = sym in cat

            # exits
            post = b[ci + 1:]
            eod_px = post[-1][4]
            # chandelier trailing
            hi_since = entry_px
            trail_exit = None
            for (ts, o, h, l, c, v) in post:
                hi_since = max(hi_since, h)
                stop = hi_since * (1 - trail / 100.0)
                if l <= stop:
                    trail_exit = min(o, stop) if o < stop else stop
                    break
            exit_trail = trail_exit if trail_exit is not None else eod_px

            qty = max(1, int(notional // entry_px))
            cA = compute_costs(entry_px, eod_px, qty, "long", "intraday")
            cB = compute_costs(entry_px, exit_trail, qty, "long", "intraday")
            netA = cA.net_pnl / (entry_px * qty) * 100
            netB = cB.net_pnl / (entry_px * qty) * 100
            trades.append(dict(date=d, sym=sym, move_pct=move_pct, pattern=pattern,
                               entry_t=hm(entry_ts), breadth=breadth, cat=has_cat,
                               netA=netA, netB=netB))
        if (di + 1) % 20 == 0:
            print(f"  ...{di+1}/{len(dates)} sessions, {len(trades)} trades", flush=True)

    conn.close()
    return trades


def stats(rows, key="netA"):
    n = len(rows)
    if not n:
        return dict(n=0)
    vals = [r[key] for r in rows]
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    gp, gl = sum(wins), -sum(losses)
    return dict(n=n, win=round(100 * len(wins) / n, 0), avg=round(sum(vals) / n, 2),
                med=round(sorted(vals)[n // 2], 2),
                pf=round(gp / gl, 2) if gl else float("inf"),
                exp=round(sum(vals) / n, 2))


def bucket(trades, label_fn, key="netA"):
    g = defaultdict(list)
    for t in trades:
        g[label_fn(t)].append(t)
    return {k: stats(v, key) for k, v in sorted(g.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/opt/nse-data-service/data/nse.db")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--min-move", type=float, default=3.0)
    ap.add_argument("--entry-t", type=float, default=3.0, help="entry confirmation %% from open")
    ap.add_argument("--breadth-t", type=float, default=1.5, help="peer 'is up' threshold %%")
    ap.add_argument("--trail", type=float, default=3.0)
    ap.add_argument("--notional", type=float, default=45000)
    ap.add_argument("--trigger", choices=["event", "cross"], default="event",
                    help="event=post-hoc move-event symbols (lookahead); cross=any symbol crossing +T (clean)")
    a = ap.parse_args()

    print(f"=== CONTEXT MOMENTUM BACKTEST · entry=+{a.entry_t}% trail={a.trail}% "
          f"notional=Rs{a.notional:.0f} ===", flush=True)
    print(f"    TRIGGER MODE = {a.trigger}"
          + ("  (CLEAN — no event conditioning)" if a.trigger == "cross"
             else "  (selection-biased — diagnostic only)"))
    trades = run(a.db, a.days, a.min_move, a.entry_t, a.breadth_t, a.trail, a.notional,
                 trigger=a.trigger)
    print(f"\nTOTAL TRADES: {len(trades)}\n")
    if not trades:
        return 0

    def show(title, d):
        print(title)
        print(f"  {'bucket':<22}{'n':>6}{'win%':>7}{'avg%':>8}{'med%':>8}{'PF':>7}")
        for k, s in d.items():
            if s.get("n"):
                print(f"  {str(k):<22}{s['n']:>6}{s['win']:>7.0f}{s['avg']:>8.2f}{s['med']:>8.2f}{s['pf']:>7.2f}")
        print()

    for ex, name in [("netA", "EXIT A — EOD square-off"), ("netB", "EXIT B — chandelier trail")]:
        print(f"\n########## {name} ##########")
        show("OVERALL (unfiltered baseline):", {"all": stats(trades, ex)})
        show("by SECTOR BREADTH (# peers already up at entry):",
             bucket(trades, lambda t: "breadth_0" if t["breadth"] == 0
                    else "breadth_1-2" if t["breadth"] <= 2 else "breadth_3plus", ex))
        show("by CATALYST present:",
             bucket(trades, lambda t: "catalyst" if t["cat"] else "no_catalyst", ex))
        show("COMBINED filter (breadth>=3 AND catalyst):",
             bucket(trades, lambda t: "PASS" if (t["breadth"] >= 3 and t["cat"]) else "fail", ex))
        show("by EOD pattern (diagnostic, NOT tradeable — lookahead):",
             bucket(trades, lambda t: t["pattern"], ex))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
