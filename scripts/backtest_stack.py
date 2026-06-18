"""Stacking test — does a factor that FAILS the standalone gate still ADD signal
when combined with the validated Q+V base? Computes every engine's cross-sectional
score once per as-of date (the expensive part), then tests many COMBINATIONS
cheaply: each combo = mean of its engines' [0,100] scores, bucketed for forward
30/60/90d sector-excess (net-of-cost), per tier. Population is held fixed to names
that have the Q+V base (so a combo is judged on the SAME stocks as the baseline).

A combo "wins" only if it beats Q+V's A+B 60d spread AND stays monotonic — then it
must still clear OOS (validate_oos) before earning weight.

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_stack.py --step 20 --lookback 400
"""
from __future__ import annotations

import argparse
import importlib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GRADES = ("A core", "B tradeable", "C volatile")
HZ = [30, 60, 90]
ENGINE_MODS = {
    "quality": "nse_data.research.quality_engine",
    "valuation": "nse_data.research.valuation_engine",
    "momentum": "nse_data.research.momentum_engine",
    "ownership": "nse_data.research.ownership_engine",
    "turnaround": "nse_data.research.turnaround_engine",
}
# combos to test (all include the Q+V base so the population is comparable)
COMBOS = [
    ("Q+V (base)",        ["quality", "valuation"]),
    ("Q+V+Mom",           ["quality", "valuation", "momentum"]),
    ("Q+V+Own",           ["quality", "valuation", "ownership"]),
    ("Q+V+Turn",          ["quality", "valuation", "turnaround"]),
    ("Q+V+Own+Turn",      ["quality", "valuation", "ownership", "turnaround"]),
    ("Q+V+Mom+Own",       ["quality", "valuation", "momentum", "ownership"]),
    ("Q+V+Mom+Own+Turn",  ["quality", "valuation", "momentum", "ownership", "turnaround"]),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--lookback", type=int, default=400)
    ap.add_argument("--cost", type=float, default=0.50)
    ap.add_argument("--buckets", type=int, default=5)
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats
    from nse_data.fundamentals.sectors import sector_class_for
    engines = {k: importlib.import_module(v) for k, v in ENGINE_MODS.items()}

    conn = open_db(args.db)
    bench_for, bseries, _ = edge_stats.bench_resolver(conn)
    grade_of = {r[0]: r[1] for r in conn.execute("SELECT symbol, grade FROM tradeable_universe")}
    syms = [s for s, g in grade_of.items() if g in GRADES]
    sec_cache: dict = {}
    def sector_of(s):
        if s not in sec_cache:
            sec_cache[s] = sector_class_for(s).value
        return sec_cache[s]

    cal = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d").fetchall()
    end = len(cal) - max(HZ) - 1
    idxs = list(range(max(0, end - args.lookback), end, args.step))
    asof = [(cal[i][0], cal[i][1]) for i in idxs]
    print(f"universe={len(syms)}  dates={len(asof)} ({asof[0][0]}..{asof[-1][0]})  "
          f"horizons={HZ}  cost={args.cost}%\n", flush=True)

    series_cache: dict = {}
    def series(s):
        if s not in series_cache:
            series_cache[s] = edge_stats.load_series(conn, s)
        return series_cache[s]

    # samples[(combo, tier, h)] = list of (combined_score, net_excess_ret)
    samples = defaultdict(list)
    for di, (d, ep) in enumerate(asof, 1):
        sc = {name: mod.score_universe(conn, syms, ep, sector_of) for name, mod in engines.items()}
        print(f"  scored date {di}/{len(asof)} {d}", flush=True)
        for sym in syms:
            if sym not in sc["quality"] or sym not in sc["valuation"]:
                continue                                    # require the Q+V base
            s, dates = series(sym)
            if d not in dates:
                continue
            i = dates.index(d)
            bser, bdates = bseries[bench_for(sector_of(sym))]
            b0 = edge_stats.close_on_or_before(bser, bdates, d)
            entry = s[d]
            nets = {}
            for h in HZ:
                if i + h < len(dates):
                    xd = dates[i + h]
                    b1 = edge_stats.close_on_or_before(bser, bdates, xd)
                    bench = (b1 / b0 - 1) * 100 if (b0 and b1) else 0.0
                    nets[h] = (s[xd] / entry - 1) * 100 - bench - args.cost
            g = grade_of.get(sym)
            for cname, cengs in COMBOS:
                vals = [sc[e][sym]["score"] for e in cengs if sym in sc[e]]
                if not vals:
                    continue
                combined = sum(vals) / len(vals)
                for h, net in nets.items():
                    samples[(cname, "ALL", h)].append((combined, net))
                    if g:
                        samples[(cname, g, h)].append((combined, net))
                    if g in ("A core", "B tradeable"):
                        samples[(cname, "A+B", h)].append((combined, net))

    def spread_mono(cname, tier, h):
        data = sorted(samples[(cname, tier, h)], key=lambda x: x[0])
        if len(data) < args.buckets * 5:
            return None
        B = args.buckets
        means = [sum(c[1] for c in data[b*len(data)//B:(b+1)*len(data)//B]) /
                 max(1, len(data[b*len(data)//B:(b+1)*len(data)//B])) for b in range(B)]
        mono = all(means[i] <= means[i+1] for i in range(len(means)-1))
        return means[-1] - means[0], mono, len(data), means

    print("\n================ STACKING RESULTS (A+B tier — the executable bar) ================")
    print(f"{'combo':<20} {'30d':>16} {'60d':>16} {'90d':>16}")
    for cname, _ in COMBOS:
        cells = []
        for h in HZ:
            r = spread_mono(cname, "A+B", h)
            cells.append("n/a" if r is None else f"{r[0]:+.1f}% {'mono' if r[1] else 'jag '}")
        print(f"{cname:<20} {cells[0]:>16} {cells[1]:>16} {cells[2]:>16}", flush=True)

    print("\n--- detail: A+B 60d buckets per combo ---")
    for cname, _ in COMBOS:
        r = spread_mono(cname, "A+B", 60)
        if r:
            sp, mono, n, means = r
            print(f"  {cname:<20} (n={n}): " + " ".join(f"Q{i+1}:{m:+.1f}" for i, m in enumerate(means))
                  + f"  spread={sp:+.1f}% mono={mono}")
    print("\n(A combo earns interest only if it beats Q+V's A+B 60d spread AND is monotonic; "
          "then it must clear OOS before any weight.)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
