"""Phase-2 validation: does the conviction score PREDICT forward returns?

Point-in-time: for a sample of past trading dates, compute each stock's score
AS-OF that date (no look-ahead) and pair it with its forward net-of-cost excess
return. Then bucket by score quantile — if mean forward return rises monotonically
with the score bucket, the score has predictive signal; if flat, it doesn't.

CAVEAT: news/analyst components are forward-only (sparse history), so historical
scores lean on momentum/drawdown/order-win. This screens the STRUCTURAL score;
the full score validates forward as daily snapshots accumulate.

    PYTHONPATH=src .venv/bin/python -u scripts/validate_score.py --horizon 10 --step 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GRADES = ("A core", "B tradeable", "C volatile")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--horizon", type=int, default=10, help="forward trading days")
    ap.add_argument("--step", type=int, default=20, help="sample an as-of date every N trading days")
    ap.add_argument("--lookback", type=int, default=260, help="trading days of history to sample")
    ap.add_argument("--cost", type=float, default=0.20)
    ap.add_argument("--buckets", type=int, default=5)
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats
    from nse_data.research.score import compute_score
    from nse_data.fundamentals.sectors import sector_class_for

    conn = open_db(args.db)
    bench_for, bseries, _ = edge_stats.bench_resolver(conn)
    syms = [r[0] for r in conn.execute(
        "SELECT symbol FROM tradeable_universe WHERE grade IN (?,?,?)", GRADES)]

    # trading calendar from the market benchmark (date, epoch)
    cal = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d").fetchall()
    if len(cal) < args.lookback:
        args.lookback = len(cal) - args.horizon - 1
    # sampled as-of indices (leave horizon room at the end)
    end = len(cal) - args.horizon - 1
    idxs = list(range(max(0, end - args.lookback), end, args.step))
    asof_dates = [(cal[i][0], cal[i][1]) for i in idxs]
    print(f"universe={len(syms)}  as-of dates={len(asof_dates)} "
          f"({asof_dates[0][0]}..{asof_dates[-1][0]})  horizon={args.horizon}d "
          f"cost={args.cost}%\n")

    samples = []          # (score, net_excess_ret)
    mtar = []             # MTARTECH trace: (date, score, net_ret)
    series_cache: dict[str, tuple] = {}
    for sym in syms:
        s, dates = edge_stats.load_series(conn, sym)
        if len(dates) < 30:
            continue
        series_cache[sym] = (s, dates)
        bser, bdates = bseries[bench_for(sector_class_for(sym).value)]
        dset = set(dates)
        for d, ep in asof_dates:
            if d not in dset:
                continue
            i = dates.index(d)
            if i + args.horizon >= len(dates):
                continue
            entry = s[d]
            exit_d = dates[i + args.horizon]
            b0 = edge_stats.close_on_or_before(bser, bdates, d)
            b1 = edge_stats.close_on_or_before(bser, bdates, exit_d)
            if not (entry and b0 and b1):
                continue
            bench = (b1 / b0 - 1) * 100
            net = (s[exit_d] / entry - 1) * 100 - bench - args.cost
            sc = compute_score(conn, sym, ep)["score"]
            samples.append((sc, net))
            if sym == "MTARTECH":
                mtar.append((d, sc, net))

    print(f"samples (stock x date)={len(samples)}\n")
    if not samples:
        print("no samples"); return 0

    # quantile buckets by score
    samples.sort(key=lambda x: x[0])
    B = args.buckets
    print(f"{'score bucket':<22}{'n':>6}{'mean score':>12}{'fwd net%':>11}{'hit%':>8}")
    print("-" * 60)
    rows = []
    for b in range(B):
        lo = b * len(samples) // B
        hi = (b + 1) * len(samples) // B
        chunk = samples[lo:hi]
        if not chunk:
            continue
        scs = [c[0] for c in chunk]
        rets = [c[1] for c in chunk]
        mean = sum(rets) / len(rets)
        hit = sum(1 for r in rets if r > 0) / len(rets) * 100
        rows.append(mean)
        print(f"Q{b+1} [{scs[0]:.2f}-{scs[-1]:.2f}]{'':<6}{len(chunk):>6}"
              f"{sum(scs)/len(scs):>12.3f}{mean:>10.2f}%{hit:>7.0f}%")
    mono = all(rows[i] <= rows[i+1] for i in range(len(rows)-1))
    print(f"\nmonotonic (low→high bucket fwd-return increasing)? {mono}")
    print(f"top bucket − bottom bucket spread: {rows[-1]-rows[0]:+.2f}%  "
          f"(positive + monotonic ⇒ score has signal)")

    if mtar:
        print(f"\n=== MTARTECH trace (score as-of date → {args.horizon}d fwd net%) ===")
        for d, sc, net in mtar:
            print(f"  {d}  score={sc:.3f}  fwd={net:+.1f}%")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
