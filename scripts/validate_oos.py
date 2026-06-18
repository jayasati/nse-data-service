"""OOS / regime-robustness check for the Q+V composite: run it over the full
available history, split the as-of dates into chronological PERIODS, and report the
B-tradeable bucket spread (60d sector-excess, net-of-cost) in EACH period. If the
edge holds across all periods it's robust; if it only shows in one, it's
regime-specific and fragile. (The composite has no fitted params, so this is a
regime test, not an overfit test.)

    PYTHONPATH=src .venv/bin/python -u scripts/validate_oos.py --periods 3 --lookback 720
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

HZ = 60


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--periods", type=int, default=3)
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--lookback", type=int, default=720)
    ap.add_argument("--cost", type=float, default=0.50)
    ap.add_argument("--buckets", type=int, default=5)
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats, composite_engine
    from nse_data.fundamentals.sectors import sector_class_for

    conn = open_db(args.db)
    bench_for, bseries, _ = edge_stats.bench_resolver(conn)
    grade_of = {r[0]: r[1] for r in conn.execute("SELECT symbol, grade FROM tradeable_universe")}
    syms = [s for s, g in grade_of.items() if g in ("A core", "B tradeable", "C volatile")]
    sec = {}
    def sector_of(s):
        if s not in sec:
            sec[s] = sector_class_for(s).value
        return sec[s]

    cal = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d").fetchall()
    end = len(cal) - HZ - 1
    idxs = list(range(max(0, end - args.lookback), end, args.step))
    asof = [(cal[i][0], cal[i][1]) for i in idxs]
    series_cache = {}
    def series(s):
        if s not in series_cache:
            series_cache[s] = edge_stats.load_series(conn, s)
        return series_cache[s]

    # collect B-tradeable samples tagged by as-of-date index
    per_date = defaultdict(list)   # date_idx -> [(score, net60)]
    for di, (d, ep) in enumerate(asof):
        scored = composite_engine.score_universe(conn, syms, ep, sector_of)
        for sym, r in scored.items():
            if grade_of.get(sym) not in ("A core", "B tradeable"):
                continue
            s, dates = series(sym)
            if d not in dates:
                continue
            i = dates.index(d)
            if i + HZ >= len(dates):
                continue
            bser, bdates = bseries[bench_for(sector_of(sym))]
            b0 = edge_stats.close_on_or_before(bser, bdates, d)
            b1 = edge_stats.close_on_or_before(bser, bdates, dates[i + HZ])
            bench = (b1 / b0 - 1) * 100 if (b0 and b1) else 0.0
            net = (s[dates[i + HZ]] / s[d] - 1) * 100 - bench - args.cost
            per_date[di].append((r["score"], net))

    print(f"A+B composite (Q+V), 60d sector-excess, cost {args.cost}% — OOS by period\n")
    P = args.periods
    n_dates = len(asof)
    for p in range(P):
        lo, hi = p * n_dates // P, (p + 1) * n_dates // P
        data = [x for di in range(lo, hi) for x in per_date[di]]
        if len(data) < args.buckets * 5:
            print(f"  period {p+1} ({asof[lo][0]}..{asof[hi-1][0]}): too few ({len(data)})"); continue
        data.sort(key=lambda x: x[0])
        B = args.buckets
        means = [sum(c[1] for c in data[b*len(data)//B:(b+1)*len(data)//B]) /
                 max(1, len(data[b*len(data)//B:(b+1)*len(data)//B])) for b in range(B)]
        mono = all(means[i] <= means[i+1] for i in range(len(means)-1))
        print(f"  period {p+1} [{asof[lo][0]}..{asof[hi-1][0]}] n={len(data)}: "
              f"Q1:{means[0]:+.1f}%  Q5:{means[-1]:+.1f}%  spread={means[-1]-means[0]:+.1f}%  mono={mono}")
    print("\n(Edge is OOS-robust if the spread is positive in EVERY period, not just one.)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
