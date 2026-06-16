"""Net-of-cost, market-adjusted backtest of the sector result-day edge (P1).

The go-live gate (plans/RESULT_EFFECT_TEST_PLAN.md). Hardens the directional seed
(backtest_sector_signals.py) into a verdict-maker by adding the rigor layer the
seed lacked:

  forward return  →  market/sector EXCESS  →  minus COSTS  →  bootstrap 95% CI  →  min-n gate

For each stored result announcement (real broadcast time from raw_announcements),
the sector engine's verdict is replayed (classify_result) and the *capturable*
forward return is measured from a realistic entry (event-day CLOSE for an
intraday disclosure; next-session OPEN for after-hours — the gap is un-capturable).
That return is then:
  • EXCESS — minus the benchmark ETF's return over the SAME dates (BANKBEES for
    bfsi, ITBEES/PHARMABEES/FMCGIETF/METALIETF for those sectors, NIFTYBEES
    otherwise). Removes market/sector beta, isolating the idiosyncratic reaction.
  • NET — minus a round-trip cost (default 0.20% delivery; the holds are overnight).
  • Direction-adjusted — sign flipped for shorts, so positive = the engine's call
    paid off.
A cell (sector × confidence × horizon) is a TRADE verdict only if n ≥ min-n AND a
10k-sample bootstrap 95% CI on the mean net excess excludes 0 on the positive side.

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_result_effect.py
    ... --scope consolidated --min-n 25 --cost-pct 0.20 --min-confidence medium
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}

# Sector class -> benchmark ETF proxy (daily returns track the index). Sectors
# without a liquid sector ETF fall back to the market (NIFTYBEES).
_MARKET_BENCH = "NIFTYBEES"
_SECTOR_BENCH = {
    "bfsi": "BANKBEES", "it": "ITBEES", "pharma": "PHARMABEES",
    "fmcg": "FMCGIETF", "metals": "METALIETF",
}

_MKT_OPEN, _MKT_CLOSE = (9, 15), (15, 30)


def _event_dt(broadcast_dt: str | None):
    if not broadcast_dt:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(broadcast_dt.strip()[:len(fmt) + 4].strip(), fmt)
        except ValueError:
            continue
    return None


def _session_kind(dt) -> str:
    if dt is None:
        return "after_hours"
    return "intraday" if _MKT_OPEN <= (dt.hour, dt.minute) <= _MKT_CLOSE else "after_hours"


def _load_series(conn, symbol: str):
    """(date->close dict, sorted dates) for a daily-candle symbol."""
    rows = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, close FROM raw_intraday_candles "
        "WHERE symbol=? AND interval='day' AND close IS NOT NULL ORDER BY ts",
        (symbol,),
    ).fetchall()
    series = {d: c for d, c in rows}
    return series, sorted(series)


def _close_on_or_before(series, dates, d):
    """Latest close on or before date `d` (nearest-prior fill for holidays)."""
    if d in series:
        return series[d]
    i = bisect.bisect_right(dates, d)
    return series[dates[i - 1]] if i else None


def _entry_forwards(conn, symbol: str, event_date: str, kind: str, horizons):
    """(entry_date, gap%, {h: (exit_date, stock_ret%)}) or None.

    intraday    → entry = event-day CLOSE (you reacted in-session), gap = None.
    after_hours → entry = NEXT session OPEN; gap% = next-open/event-close − 1."""
    rows = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, open, close FROM raw_intraday_candles "
        "WHERE symbol=? AND interval='day' AND date(ts,'unixepoch','+05:30') >= ? "
        "ORDER BY ts LIMIT ?",
        (symbol, event_date, max(horizons) + 3),
    ).fetchall()
    bars = [(d, o, c) for d, o, c in rows if c]
    if not bars:
        return None
    if kind == "intraday":
        entry_idx, entry, gap = 0, bars[0][2], None
    else:
        if len(bars) < 2 or not bars[0][2] or not bars[1][1]:
            return None
        entry_idx, entry = 1, bars[1][1]
        gap = (bars[1][1] / bars[0][2] - 1.0) * 100.0
    if not entry:
        return None
    entry_date = bars[entry_idx][0]
    out = {}
    for h in horizons:
        j = entry_idx + h
        if j < len(bars) and bars[j][2]:
            out[h] = (bars[j][0], (bars[j][2] / entry - 1.0) * 100.0)
    return entry_date, gap, out


def _bootstrap_ci(samples, iters: int, seed: int = 12345):
    """(lo, hi) 95% CI on the mean via percentile bootstrap. None if <2 samples."""
    n = len(samples)
    if n < 2:
        return None
    rnd = random.Random(seed)
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += samples[rnd.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * iters)]
    hi = means[min(int(0.975 * iters), iters - 1)]
    return lo, hi


def _verdict(n, ci, min_n) -> str:
    if n < min_n:
        return "INSUFFICIENT"
    if ci is None:
        return "NO-EDGE"
    lo, hi = ci
    if lo > 0:
        return "TRADE"
    if hi < 0:
        return "AVOID(backwards)"
    return "NO-EDGE"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--scope", default="standalone", choices=("standalone", "consolidated"))
    ap.add_argument("--sector", help="restrict to one sector class")
    ap.add_argument("--min-confidence", default="low", choices=("low", "medium", "high"))
    ap.add_argument("--horizons", default="1,2,3,5,10,20")
    ap.add_argument("--primary-horizon", type=int, default=1,
                    help="fixed horizon the verdict is judged at (avoids cherry-picking "
                         "the best of N horizons — that selection inflates the edge)")
    ap.add_argument("--since", default="2023-06-13", help="event-date floor (data start)")
    ap.add_argument("--cost-pct", type=float, default=0.20,
                    help="round-trip cost subtracted per trade (delivery default)")
    ap.add_argument("--min-n", type=int, default=30, help="min events for a verdict")
    ap.add_argument("--bootstrap", type=int, default=10000)
    args = ap.parse_args()
    horizons = [int(x) for x in args.horizons.split(",")]
    h0 = horizons[0]

    from nse_data.storage.db import open_db
    from nse_data.fundamentals.from_results import is_result_subject
    from nse_data.fundamentals.sectors import classify_result, sector_class_for

    conn = open_db(args.db)

    # Pre-load every benchmark ETF series once.
    benches = {_MARKET_BENCH, *_SECTOR_BENCH.values()}
    series = {b: _load_series(conn, b) for b in benches}
    avail = {b: len(series[b][0]) for b in benches}

    def bench_for(sector: str) -> str:
        b = _SECTOR_BENCH.get(sector, _MARKET_BENCH)
        return b if avail.get(b) else _MARKET_BENCH      # fall back if ETF missing

    anns = conn.execute(
        "SELECT symbol, subject, broadcast_dt FROM raw_announcements "
        "WHERE broadcast_dt IS NOT NULL ORDER BY broadcast_dt",
    ).fetchall()

    min_rank = _CONF_RANK[args.min_confidence]
    # cell key -> horizon -> list of NET EXCESS returns (direction-adjusted)
    cells: dict[tuple, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    bench_used: dict[str, str] = {}
    gaps: dict[str, list[float]] = defaultdict(list)
    n_events = n_signals = n_priced = 0
    seen: set[tuple] = set()

    for symbol, subject, bdt in anns:
        if not is_result_subject(subject):
            continue
        dt = _event_dt(bdt)
        ed = dt.date().isoformat() if dt else None
        if not ed or ed < args.since:
            continue
        row = conn.execute(
            "SELECT period_ending, growth_json, narrative_json FROM extracted_financials "
            "WHERE symbol=? AND scope=? AND growth_json IS NOT NULL "
            "AND period_ending < ? AND julianday(?) - julianday(period_ending) <= 100 "
            "ORDER BY period_ending DESC LIMIT 1",
            (symbol, args.scope, ed, ed),
        ).fetchone()
        if row is None:
            continue
        period, gjson, njson = row
        if (symbol, period) in seen:
            continue
        seen.add((symbol, period))
        sector = sector_class_for(symbol).value
        if args.sector and sector != args.sector.lower():
            continue
        n_events += 1
        growth = json.loads(gjson) if gjson else None
        narrative = json.loads(njson) if njson else None
        v = classify_result(symbol, growth, None, narrative=narrative)
        if v.direction is None or _CONF_RANK.get(v.confidence, 0) < min_rank:
            continue
        n_signals += 1
        kind = _session_kind(dt)
        res = _entry_forwards(conn, symbol, ed, kind, horizons)
        if res is None:
            continue
        entry_date, gap, fwd = res
        if not fwd:
            continue
        bench = bench_for(sector)
        bser, bdates = series[bench]
        bench_used[sector] = bench
        n_priced += 1
        sign = 1.0 if v.direction == "long" else -1.0
        if gap is not None:
            gaps[kind].append(gap * sign)
        for h, (exit_date, stock_ret) in fwd.items():
            b0 = _close_on_or_before(bser, bdates, entry_date)
            b1 = _close_on_or_before(bser, bdates, exit_date)
            bench_ret = (b1 / b0 - 1.0) * 100.0 if (b0 and b1) else 0.0
            net = (stock_ret - bench_ret) * sign - args.cost_pct   # excess, then cost
            keys = [(sector, v.confidence), (sector, "ALL"),
                    ("ALL", "ALL"), ("TIMING", kind)]
            if kind == "intraday":            # the capturable subset, per sector + overall
                keys.append((sector, "intra"))
                keys.append(("ALL", "intra"))
            for k in keys:
                cells[k][h].append(net)

    conn.close()

    print(f"\n=== RESULT-EFFECT NET-OF-COST BACKTEST ===")
    print(f"window≥{args.since} scope={args.scope} min_conf={args.min_confidence} "
          f"cost={args.cost_pct}%/trade min_n={args.min_n} bootstrap={args.bootstrap}")
    print(f"events={n_events} signals(directional,gated)={n_signals} priced={n_priced}")
    print("benchmark ETF availability (bars): "
          + ", ".join(f"{b}={avail[b]}" for b in sorted(avail)))
    print("sector→benchmark: "
          + ", ".join(f"{s}:{b}" for s, b in sorted(bench_used.items())) + "\n")

    # ---- full grid: net excess % (hit%) per (sector,conf) × horizon ----
    hdr = f"{'sector':<10}{'conf':<7}{'n':>5} " + "".join(f"{'net@'+str(h):>9}" for h in horizons)
    print(hdr); print("-" * len(hdr))
    def _order(k): return (k[0] != "ALL", k[0] != "TIMING", k)
    for key in sorted(cells, key=_order):
        sector, conf = key
        n = len(cells[key][h0])
        if not n:
            continue
        line = f"{sector:<10}{conf:<7}{n:>5} "
        for h in horizons:
            rets = cells[key][h]
            line += f"{(sum(rets)/len(rets)):>8.2f}%" if rets else f"{'—':>9}"
        print(line)

    # ---- verdict table: judged at the FIXED primary horizon, with 95% CI ----
    ph = args.primary_horizon
    print(f"\n=== PER-CELL VERDICT @ {ph}d (fixed; CI excl. 0 ⇒ TRADE) ===")
    print(f"{'sector':<10}{'conf':<7}{'n':>5}{'net%':>8}{'hit%':>6}"
          f"{'95% CI':>18}  verdict")
    print("-" * 72)
    rowsout = []
    for key in sorted(cells, key=_order):
        sector, conf = key
        if sector == "TIMING":
            continue
        rets = cells[key].get(ph, [])
        if not rets:
            continue
        mean_net = sum(rets) / len(rets)
        hit = sum(1 for r in rets if r > 0) / len(rets) * 100
        ci = _bootstrap_ci(rets, args.bootstrap)
        verd = _verdict(len(rets), ci, args.min_n)
        ci_txt = f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if ci else "—"
        rowsout.append((verd, sector, conf, len(rets), mean_net, hit, ci_txt))
        print(f"{sector:<10}{conf:<7}{len(rets):>5}{mean_net:>7.2f}%{hit:>5.0f}%"
              f"{ci_txt:>18}  {verd}")

    tradeable = [r for r in rowsout if r[0] == "TRADE" and r[1] != "ALL"]
    print(f"\nTRADE cells @ {ph}d: {len(tradeable)}  ·  "
          + (", ".join(f"{r[1]}/{r[2]}({r[4]:+.2f}%)" for r in tradeable) or "none"))

    # ---- timing breakdown ----
    print(f"\n{'timing':<12}{'n':>5}{'avg gap%':>10}" + "".join(f"{'net@'+str(h):>9}" for h in horizons))
    print("-" * (27 + 9 * len(horizons)))
    for kind in ("intraday", "after_hours"):
        key = ("TIMING", kind)
        n = len(cells[key][h0]) if key in cells else 0
        if not n:
            continue
        g = gaps.get(kind, [])
        gtxt = f"{sum(g)/len(g):>9.2f}%" if g else f"{'—':>10}"
        line = f"{kind:<12}{n:>5}{gtxt}"
        for h in horizons:
            rets = cells[key][h]
            line += f"{(sum(rets)/len(rets)):>8.2f}%" if rets else f"{'—':>9}"
        print(line)
    print("\n(net@h = direction-adjusted EXCESS return over the benchmark ETF, minus "
          f"{args.cost_pct}% round-trip cost, h sessions after a realistic entry.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
