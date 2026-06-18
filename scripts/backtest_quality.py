"""Validate the Quality engine: does its cross-sectional score RANK forward
sector-excess returns at 30/60/90d? Bucketed, net-of-cost, reported for ALL and
the A+B tradeable tier (the honest bar — never flattered by C-volatile).

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_quality.py --step 20 --lookback 400
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GRADES = ("A core", "B tradeable", "C volatile")
HZ = [30, 60, 90]


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
    from nse_data.research.quality_engine import score_universe
    from nse_data.fundamentals.sectors import sector_class_for

    conn = open_db(args.db)
    bench_for, bseries, _ = edge_stats.bench_resolver(conn)
    grade_of = {r[0]: r[1] for r in conn.execute("SELECT symbol, grade FROM tradeable_universe")}
    syms = [s for s, g in grade_of.items() if g in GRADES]
    sec_cache: dict[str, str] = {}
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
    print(f"universe={len(syms)}  as-of dates={len(asof)} ({asof[0][0]}..{asof[-1][0]})  "
          f"horizons={HZ}  cost={args.cost}%\n")

    series_cache: dict[str, tuple] = {}
    def series(s):
        if s not in series_cache:
            series_cache[s] = edge_stats.load_series(conn, s)
        return series_cache[s]

    # samples[(tier_key, horizon)] = list of (score, net_excess_ret)
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
            for h in HZ:
                if i + h >= len(dates):
                    continue
                xd = dates[i + h]
                b1 = edge_stats.close_on_or_before(bser, bdates, xd)
                bench = (b1 / b0 - 1) * 100 if (b0 and b1) else 0.0
                net = (s[xd] / entry - 1) * 100 - bench - args.cost
                samples[("ALL", h)].append((r["score"], net))
                if grade_of.get(sym) in ("A core", "B tradeable"):
                    samples[("A+B", h)].append((r["score"], net))

    def report(tier):
        print(f"--- {tier} tier ---")
        for h in HZ:
            data = sorted(samples[(tier, h)], key=lambda x: x[0])
            if len(data) < args.buckets * 5:
                print(f"  {h}d: too few samples ({len(data)})"); continue
            B = args.buckets
            means = []
            cells = []
            for b in range(B):
                lo, hi = b * len(data) // B, (b + 1) * len(data) // B
                chunk = data[lo:hi]
                m = sum(c[1] for c in chunk) / len(chunk)
                means.append(m)
                cells.append(f"Q{b+1}:{m:+.1f}%")
            mono = all(means[i] <= means[i+1] for i in range(len(means)-1))
            print(f"  {h}d (n={len(data)}): " + "  ".join(cells)
                  + f"   spread(Q5-Q1)={means[-1]-means[0]:+.1f}%  monotonic={mono}")

    report("ALL")
    print()
    report("A+B")
    print("\n(Quality earns weight only if higher buckets show higher forward sector-excess "
          "on A+B, monotonic + positive spread, at this cost.)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
