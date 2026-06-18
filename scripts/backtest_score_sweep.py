"""Cost + parameter sweep of the E2 score strategy. The expensive part (computing
the point-in-time score series) is done ONCE per stock; then we simulate multiple
threshold sets and apply multiple cost levels cheaply against the cached scores.
Answers: does the +0.52% survive realistic cost, and is it stable across params —
especially OUTSIDE the fragile C-volatile tier?

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_score_sweep.py --lookback 220
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GRADES = ("A core", "B tradeable", "C volatile")
PARAMS = [(0.58, 0.45, 0.15), (0.62, 0.42, 0.12), (0.55, 0.50, 0.20)]  # (t_in,t_out,trail)
COSTS = [0.20, 0.50, 0.80]
MAX_HOLD = 60


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--lookback", type=int, default=220)
    ap.add_argument("--grades", default=",".join(GRADES))
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats
    from nse_data.research.score import compute_score
    from nse_data.fundamentals.sectors import sector_class_for

    conn = open_db(args.db)
    bench_for, bseries, _ = edge_stats.bench_resolver(conn)
    grades = tuple(g.strip() for g in args.grades.split(",") if g.strip())
    ph = ",".join("?" * len(grades))
    syms = [r[0] for r in conn.execute(
        f"SELECT symbol FROM tradeable_universe WHERE grade IN ({ph})", grades)]
    grade_of = {r[0]: r[1] for r in conn.execute("SELECT symbol, grade FROM tradeable_universe")}

    cal = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d").fetchall()
    cal = cal[-args.lookback:]
    win_dates = [d for d, _ in cal]
    win_eps = {d: ep for d, ep in cal}

    # --- expensive pass ONCE: score series + candle/bench per stock ---
    print(f"computing score series for {len(syms)} stocks x {len(win_dates)} days…", flush=True)
    data = {}
    for i, sym in enumerate(syms, 1):
        s, dates = edge_stats.load_series(conn, sym)
        if len(dates) < 30:
            continue
        dset = set(dates)
        bser, bdates = bseries[bench_for(sector_class_for(sym).value)]
        sc = {d: compute_score(conn, sym, win_eps[d])["score"] for d in win_dates if d in dset}
        data[sym] = (s, dates, sc, bser, bdates)
        if i % 100 == 0:
            print(f"  {i}/{len(syms)}", flush=True)

    def simulate(t_in, t_out, trail):
        """Return trades as (grade, stock_ret%, bench_ret%) — cost-independent."""
        trades = []
        for sym, (s, dates, sc, bser, bdates) in data.items():
            pos = None
            for d in win_dates:
                if d not in sc:
                    continue
                v = sc[d]
                if pos is None:
                    if v >= t_in:
                        pos = {"d": d, "px": s[d], "peak": v}
                else:
                    pos["peak"] = max(pos["peak"], v)
                    held = dates.index(d) - dates.index(pos["d"])
                    if v < t_out or (pos["peak"] - v) >= trail or held >= MAX_HOLD or d == win_dates[-1]:
                        b0 = edge_stats.close_on_or_before(bser, bdates, pos["d"])
                        b1 = edge_stats.close_on_or_before(bser, bdates, d)
                        bench = (b1 / b0 - 1) * 100 if (b0 and b1) else 0.0
                        trades.append((grade_of.get(sym, "?"), (s[d] / pos["px"] - 1) * 100, bench))
                        pos = None
        return trades

    def stats(trades, cost, tiers=None):
        sel = [t for t in trades if tiers is None or t[0] in tiers]
        if not sel:
            return None
        nets = [st - bn - cost for (_, st, bn) in sel]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        pf = (sum(wins) / -sum(losses)) if losses and sum(losses) else 99.0
        return (len(nets), sum(nets) / len(nets), 100 * len(wins) / len(nets), pf)

    print("\n=== SWEEP: expectancy(net excess) / win% / profit-factor ===")
    AB = {"A core", "B tradeable"}
    C = {"C volatile"}
    for p in PARAMS:
        trades = simulate(*p)
        print(f"\nparams t_in={p[0]} t_out={p[1]} trail={p[2]}  (trades={len(trades)})")
        print(f"  {'cost':>5} | {'ALL exp/win/pf':>22} | {'A+B exp/win/pf':>22} | {'C-vol exp/win/pf':>22}")
        for c in COSTS:
            a = stats(trades, c)
            ab = stats(trades, c, AB)
            cv = stats(trades, c, C)
            def fmt(x):
                return f"{x[1]:+.2f}%/{x[2]:.0f}%/{x[3]:.2f} (n={x[0]})" if x else "—"
            print(f"  {c:>5.2f} | {fmt(a):>22} | {fmt(ab):>22} | {fmt(cv):>22}")
    print("\n(A+B is the tradeable tier with realistic execution. If A+B expectancy "
          "stays positive at cost>=0.5%, that's a genuine signal; if only C-vol survives, "
          "it's likely under-costed slippage.)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
