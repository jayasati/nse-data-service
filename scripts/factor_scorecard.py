"""Factor research scorecard — the "do my factors have predictive power individually?"
report institutional teams run BEFORE any model. For every factor engine, over a grid
of historical as-of dates, measures vs forward sector-excess (net-of-cost) at 30/60/90/120d:

  * Rank IC   — mean over dates of the cross-sectional Spearman(score, forward excess)
  * IR        — Rank IC / its date-to-date std  (stability; |IR|>~0.5 is meaningful)
  * hit       — fraction of dates with positive IC
  * spread    — top-minus-bottom decile mean forward excess (pooled)
  * mono      — is the 10-decile lift monotonic low→high

    PYTHONPATH=src .venv/bin/python -u scripts/factor_scorecard.py

Keep factors with a positive, stable Rank IC (IR) AND a monotone decile spread; the
rest are noise — exactly the factors the validation gate already excluded from the
composite. This is the per-factor evidence behind that scorecard.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

HZ = (30, 60, 90, 120)
# return-predicting factors (confidence is coverage, not a return factor → excluded)
FACTORS = ("quality", "valuation", "momentum", "turnaround",
           "liquidity", "surprise", "ownership", "risk")
MIN_XS = 20          # minimum cross-section per date to trust that date's IC


def _spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    def ranks(a):
        order = sorted(range(n), key=lambda i: a[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and a[order[j + 1]] == a[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)) * sum((ry[i] - my) ** 2 for i in range(n)))
    return num / den if den else None


def _decile_spread(pairs):
    """pairs: [(score, fwd)] pooled. Returns (top-bottom, monotone) over 10 deciles."""
    if len(pairs) < 50:
        return None, None
    pairs = sorted(pairs, key=lambda x: x[0])
    m, B = len(pairs), 10
    means = [sum(c[1] for c in pairs[b * m // B:(b + 1) * m // B]) /
             max(1, len(pairs[b * m // B:(b + 1) * m // B])) for b in range(B)]
    mono = all(means[i] <= means[i + 1] for i in range(B - 1))
    return means[-1] - means[0], mono


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--lookback", type=int, default=500)
    ap.add_argument("--cost", type=float, default=0.50)
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats
    from nse_data.research.snapshot import ENGINES as ALL
    from nse_data.fundamentals.sectors import sector_class_for

    engines = [(n, m) for n, m in ALL if n in FACTORS]
    conn = open_db(args.db)
    bench_for, bseries, _ = edge_stats.bench_resolver(conn)
    grade_of = {r[0]: r[1] for r in conn.execute("SELECT symbol, grade FROM tradeable_universe")}
    syms = [s for s, g in grade_of.items() if g in ("A core", "B tradeable", "C volatile")]
    sec_cache = {}
    def sector_of(s):
        if s not in sec_cache:
            sec_cache[s] = sector_class_for(s).value
        return sec_cache[s]
    series_cache = {}
    def series(s):
        if s not in series_cache:
            series_cache[s] = edge_stats.load_series(conn, s)
        return series_cache[s]

    cal = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d").fetchall()
    end = len(cal) - max(HZ) - 1
    idxs = list(range(max(0, end - args.lookback), end, args.step))
    asof = [(cal[i][0], cal[i][1]) for i in idxs]
    print(f"factors={len(engines)}  universe={len(syms)}  dates={len(asof)} "
          f"({asof[0][0]}..{asof[-1][0]})  horizons={HZ}  cost={args.cost}%\n")

    # per (factor, horizon): list of per-date ICs + pooled (score,fwd) for deciles
    date_ic = defaultdict(list)
    pooled = defaultdict(list)
    for d, ep in asof:
        # forward excess per symbol per horizon, computed once
        fwd = {}
        for sym in syms:
            ss, dts = series(sym)
            if d not in dts:
                continue
            i = dts.index(d)
            bser, bdates = bseries[bench_for(sector_of(sym))]
            b0 = edge_stats.close_on_or_before(bser, bdates, d)
            hz = {}
            for h in HZ:
                if i + h >= len(dts):
                    continue
                b1 = edge_stats.close_on_or_before(bser, bdates, dts[i + h])
                bench = (b1 / b0 - 1) * 100 if (b0 and b1) else 0.0
                hz[h] = (ss[dts[i + h]] / ss[d] - 1) * 100 - bench - args.cost
            if hz:
                fwd[sym] = hz
        for name, mod in engines:
            scored = mod.score_universe(conn, syms, ep, sector_of)
            for h in HZ:
                xs, ys = [], []
                for sym, r in scored.items():
                    if sym in fwd and h in fwd[sym]:
                        xs.append(r["score"]); ys.append(fwd[sym][h])
                        pooled[(name, h)].append((r["score"], fwd[sym][h]))
                if len(xs) >= MIN_XS:
                    ic = _spearman(xs, ys)
                    if ic is not None:
                        date_ic[(name, h)].append(ic)

    print(f"  {'factor':<11}" + "".join(f"{'IC@'+str(h):>9}{'IR':>6}{'hit':>5}{'spr':>7}{'mono':>5}" for h in HZ))
    for name, _ in engines:
        line = f"  {name:<11}"
        for h in HZ:
            ics = date_ic[(name, h)]
            if not ics:
                line += f"{'·':>9}{'·':>6}{'·':>5}{'·':>7}{'·':>5}"; continue
            mean = sum(ics) / len(ics)
            sd = (sum((x - mean) ** 2 for x in ics) / len(ics)) ** 0.5
            ir = mean / sd if sd else 0.0
            hit = sum(1 for x in ics if x > 0) / len(ics)
            spr, mono = _decile_spread(pooled[(name, h)])
            line += (f"{mean:>+9.3f}{ir:>+6.2f}{hit:>5.0%}"
                     + (f"{spr:>+7.1f}" if spr is not None else f"{'·':>7}")
                     + (f"{'Y' if mono else 'n':>5}" if mono is not None else f"{'·':>5}"))
        print(line)
    print("\n  IC = mean cross-sectional Rank IC | IR = IC/std(IC) | hit = % dates IC>0 |"
          " spr = top-bottom decile excess | mono = decile-monotone")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
