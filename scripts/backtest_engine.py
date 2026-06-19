"""Reusable engine validation rig (P0). For any engine exposing
score_universe(conn, symbols, as_of_ep, sector_of), bucket its cross-sectional
score and measure forward 30/60/90d sector-excess (net-of-cost), per grade tier.

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_engine.py --engine valuation
"""
from __future__ import annotations

import argparse
import importlib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GRADES = ("A core", "B tradeable", "C volatile")
HZ = [30, 60, 90, 120]
ENGINES = {
    "quality": "nse_data.research.quality_engine",
    "valuation": "nse_data.research.valuation_engine",
    "momentum": "nse_data.research.momentum_engine",
    "turnaround": "nse_data.research.turnaround_engine",
    "improvement": "nse_data.research.improvement_engine",
    "liquidity": "nse_data.research.liquidity_engine",
    "surprise": "nse_data.research.surprise_engine",
    "news": "nse_data.research.news_engine",
    "credibility": "nse_data.research.credibility_engine",
    "composite": "nse_data.research.composite_engine",
    "buyscore": "nse_data.research.buy_score_engine",
    "buyscore_adaptive": "nse_data.research.buy_score_engine_adaptive",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, choices=list(ENGINES))
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--lookback", type=int, default=400)
    ap.add_argument("--cost", type=float, default=0.50)
    ap.add_argument("--buckets", type=int, default=5)
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats
    from nse_data.fundamentals.sectors import sector_class_for
    score_universe = importlib.import_module(ENGINES[args.engine]).score_universe

    conn = open_db(args.db)
    bench_for, bseries, _ = edge_stats.bench_resolver(conn)
    grade_of = {r[0]: r[1] for r in conn.execute("SELECT symbol, grade FROM tradeable_universe")}
    syms = [s for s, g in grade_of.items() if g in GRADES]
    sec_cache = {}
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
    print(f"engine={args.engine}  universe={len(syms)}  dates={len(asof)} "
          f"({asof[0][0]}..{asof[-1][0]})  horizons={HZ}  cost={args.cost}%\n")

    series_cache = {}
    def series(s):
        if s not in series_cache:
            series_cache[s] = edge_stats.load_series(conn, s)
        return series_cache[s]

    samples = defaultdict(list)
    for d, ep in asof:
        scored = score_universe(conn, syms, ep, sector_of)
        for sym, r in scored.items():
            s, dates = series(sym)
            if d not in dates:
                continue
            i = dates.index(d)
            bser, bdates = bseries[bench_for(sector_of(sym))]
            b0 = edge_stats.close_on_or_before(bser, bdates, d)
            entry = s[d]
            g = grade_of.get(sym)
            for h in HZ:
                if i + h >= len(dates):
                    continue
                xd = dates[i + h]
                b1 = edge_stats.close_on_or_before(bser, bdates, xd)
                bench = (b1 / b0 - 1) * 100 if (b0 and b1) else 0.0
                net = (s[xd] / entry - 1) * 100 - bench - args.cost
                samples[("ALL", h)].append((r["score"], net))
                if g:
                    samples[(g, h)].append((r["score"], net))
                if g in ("A core", "B tradeable"):
                    samples[("A+B", h)].append((r["score"], net))

    def report(tier):
        print(f"--- {tier} ---")
        for h in HZ:
            data = sorted(samples[(tier, h)], key=lambda x: x[0])
            if len(data) < args.buckets * 5:
                print(f"  {h}d: too few ({len(data)})"); continue
            B = args.buckets
            means = [sum(c[1] for c in data[b*len(data)//B:(b+1)*len(data)//B]) /
                     max(1, len(data[b*len(data)//B:(b+1)*len(data)//B])) for b in range(B)]
            mono = all(means[i] <= means[i+1] for i in range(len(means)-1))
            from nse_data.ml.eval import spearman_ic
            ic = spearman_ic([c[0] for c in data], [c[1] for c in data])
            print(f"  {h}d (n={len(data)}): " + "  ".join(f"Q{i+1}:{m:+.1f}%" for i, m in enumerate(means))
                  + f"   spread={means[-1]-means[0]:+.1f}%  IC={ic:+.3f}  mono={mono}")

    for tier in ("ALL", "A+B", "A core", "B tradeable", "C volatile"):
        report(tier); print()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
