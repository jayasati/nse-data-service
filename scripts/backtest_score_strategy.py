"""E2 — path-dependent STRATEGY backtest of the conviction score (the real verdict
for buy-high / sell-on-drop, which the fixed-horizon test mis-judges).

Walks every trading day in the window; per stock, point-in-time score drives:
  ENTER when score >= --t-in
  HOLD, recompute daily
  EXIT when score < --t-out, OR score drops --trail from its peak-while-held,
       OR held >= --max-hold, OR window ends (mark-to-market)
Records realized net-of-cost EXCESS return per trade (vs sector benchmark) and
aggregates: trades, win%, avg win/loss, profit factor, expectancy, avg hold.

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_score_strategy.py \
        --t-in 0.58 --t-out 0.45 --trail 0.15 --max-hold 60 --lookback 220
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GRADES = ("A core", "B tradeable", "C volatile")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--t-in", type=float, default=0.58, help="enter when score >= this")
    ap.add_argument("--t-out", type=float, default=0.45, help="exit when score < this")
    ap.add_argument("--trail", type=float, default=0.15, help="exit if score drops this from peak-held")
    ap.add_argument("--max-hold", type=int, default=60, help="max hold (trading days)")
    ap.add_argument("--cost", type=float, default=0.20, help="round-trip cost %%")
    ap.add_argument("--lookback", type=int, default=220, help="trading days to simulate")
    ap.add_argument("--symbols", help="comma list (default: tracked universe)")
    ap.add_argument("--grades", default=",".join(GRADES))
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats
    from nse_data.research.score import compute_score
    from nse_data.fundamentals.sectors import sector_class_for

    conn = open_db(args.db)
    bench_for, bseries, _ = edge_stats.bench_resolver(conn)
    grades = tuple(g.strip() for g in args.grades.split(",") if g.strip())
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        ph = ",".join("?" * len(grades))
        syms = [r[0] for r in conn.execute(
            f"SELECT symbol FROM tradeable_universe WHERE grade IN ({ph})", grades)]

    cal = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d").fetchall()
    cal = cal[-args.lookback:]
    win_dates = [d for d, _ in cal]
    win_eps = {d: ep for d, ep in cal}

    trades = []                 # (symbol, grade, entry_d, exit_d, held, net_excess, reason)
    grade_of = {r[0]: r[1] for r in conn.execute(
        "SELECT symbol, grade FROM tradeable_universe")}
    for sym in syms:
        s, dates = edge_stats.load_series(conn, sym)
        if len(dates) < 30:
            continue
        dset = set(dates)
        bser, bdates = bseries[bench_for(sector_class_for(sym).value)]
        pos = None  # dict(entry_d, entry_px, peak)
        for d in win_dates:
            if d not in dset:
                continue
            sc = compute_score(conn, sym, win_eps[d])["score"]
            px = s[d]
            if pos is None:
                if sc >= args.t_in:
                    pos = {"d": d, "px": px, "peak": sc}
            else:
                pos["peak"] = max(pos["peak"], sc)
                held = dates.index(d) - dates.index(pos["d"])
                reason = ("score_out" if sc < args.t_out else
                          "trail" if (pos["peak"] - sc) >= args.trail else
                          "max_hold" if held >= args.max_hold else
                          "window_end" if d == win_dates[-1] else None)
                if reason:
                    b0 = edge_stats.close_on_or_before(bser, bdates, pos["d"])
                    b1 = edge_stats.close_on_or_before(bser, bdates, d)
                    bench = (b1 / b0 - 1) * 100 if (b0 and b1) else 0.0
                    net = (px / pos["px"] - 1) * 100 - bench - args.cost
                    trades.append((sym, grade_of.get(sym, "?"), pos["d"], d, held, net, reason))
                    pos = None

    if not trades:
        print("no trades"); return 0
    rets = [t[5] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) != 0 else float("inf")
    from collections import Counter
    print(f"=== E2 STRATEGY BACKTEST (buy>={args.t_in}, exit<{args.t_out} / trail {args.trail} / "
          f"max {args.max_hold}d, cost {args.cost}%) ===")
    print(f"window={win_dates[0]}..{win_dates[-1]}  universe={len(syms)}\n")
    print(f"  trades={len(trades)}")
    print(f"  win-rate={100*len(wins)/len(trades):.0f}%")
    print(f"  expectancy (mean net excess/trade)={sum(rets)/len(rets):+.2f}%")
    print(f"  avg win={sum(wins)/len(wins) if wins else 0:+.2f}%  avg loss={sum(losses)/len(losses) if losses else 0:+.2f}%")
    print(f"  profit factor={pf:.2f}")
    print(f"  avg hold={sum(t[4] for t in trades)/len(trades):.0f} trading days")
    print(f"  exit reasons:", dict(Counter(t[6] for t in trades)))
    bg = defaultdict(list)
    for t in trades:
        bg[t[1]].append(t[5])
    print("\n  by grade (liquidity tier):")
    for g, v in bg.items():
        print(f"    {g:<14} trades={len(v):<5} expectancy={sum(v)/len(v):+.2f}%  win={100*sum(1 for x in v if x>0)/len(v):.0f}%")
    print("\n  (net excess = realized stock return vs sector benchmark, minus round-trip cost. "
          "Positive expectancy net-of-cost = the score works as a managed buy/sell system.)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
