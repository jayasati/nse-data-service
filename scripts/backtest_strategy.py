"""E2 — PATH-DEPENDENT STRATEGY backtest (the real verdict). Unlike the E1
fixed-horizon screen (bucket score → hold blindly 30/60/90d), this trades the
score the way you actually would: BUY when the composite enters the top tier,
HOLD while it stays strong, SELL when it falls below a floor OR trails off its
peak-while-held — capturing the gain near the peak and exiting before the decline
the falling score warns about. A factor that fails E1 can still pay here (good
exits), and vice-versa (whipsaw + per-trade cost) — so we run it, not assume.

Method: score the universe at a regular cadence (default weekly) point-in-time;
per stock walk that score path through a state machine; record realized
net-of-cost trades; aggregate per-trade economics + an equal-weight portfolio
equity curve vs buy-and-hold the market (NIFTYBEES).

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_strategy.py --engine composite
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GRADES = ("A core", "B tradeable")          # executable tier only
ENGINE_MODS = {
    "composite": "nse_data.research.composite_engine",
    "quality": "nse_data.research.quality_engine",
    "valuation": "nse_data.research.valuation_engine",
    "momentum": "nse_data.research.momentum_engine",
    "ownership": "nse_data.research.ownership_engine",
}


def _d(s):
    return _dt.date.fromisoformat(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--engine", default="composite", choices=list(ENGINE_MODS))
    ap.add_argument("--cadence", type=int, default=5, help="trading days between score recompute")
    ap.add_argument("--lookback", type=int, default=520, help="trading days of history")
    ap.add_argument("--t-in", type=float, default=80.0, help="enter when score >= this")
    ap.add_argument("--t-out", type=float, default=60.0, help="exit when score < this")
    ap.add_argument("--trail", type=float, default=15.0, help="exit when score drops this off its held-peak")
    ap.add_argument("--max-hold", type=int, default=120, help="max calendar days held")
    ap.add_argument("--stop", type=float, default=-15.0, help="hard stop-loss %")
    ap.add_argument("--cost", type=float, default=1.0, help="round-trip cost %")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats
    from nse_data.fundamentals.sectors import sector_class_for
    score_universe = importlib.import_module(ENGINE_MODS[args.engine]).score_universe

    conn = open_db(args.db)
    grade_of = {r[0]: r[1] for r in conn.execute("SELECT symbol, grade FROM tradeable_universe")}
    syms = [s for s, g in grade_of.items() if g in GRADES]
    sec_cache: dict = {}
    def sector_of(s):
        if s not in sec_cache:
            sec_cache[s] = sector_class_for(s).value
        return sec_cache[s]

    cal = [r[0] for r in conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d")]
    cal = cal[-args.lookback:]
    cdates = cal[::args.cadence]                       # the score-recompute dates
    eps = {d: ep for d, ep in conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d")}
    print(f"engine={args.engine}  universe={len(syms)}  cadence={args.cadence}d  "
          f"score-dates={len(cdates)} ({cdates[0]}..{cdates[-1]})  "
          f"rules: in>={args.t_in} out<{args.t_out} trail={args.trail} "
          f"maxhold={args.max_hold}d stop={args.stop}% cost={args.cost}%\n", flush=True)

    # price series (cached) + score path per symbol on cadence dates
    series_cache: dict = {}
    def price(sym, d):
        if sym not in series_cache:
            series_cache[sym] = edge_stats.load_series(conn, sym)
        s, _dates = series_cache[sym]
        return s.get(d)

    scores: dict = {s: {} for s in syms}              # sym -> {date: score}
    for k, d in enumerate(cdates, 1):
        scored = score_universe(conn, syms, eps[d], sector_of)
        for sym, r in scored.items():
            scores[sym][d] = r["score"]
        if k % 10 == 0:
            print(f"  scored {k}/{len(cdates)} {d}", flush=True)

    # --- per-stock state machine over the cadence dates ---------------------
    trades = []                                        # (sym, d_in, d_out, gross%, net%, hold_days)
    held_by_interval = [set() for _ in range(len(cdates))]   # which syms held during [k, k+1)
    for sym in syms:
        held = False
        entry_px, entry_d, entry_k, peak = 0.0, "", 0, 0.0   # only read while held
        for k, d in enumerate(cdates):
            sc = scores[sym].get(d)
            px = price(sym, d)
            if px is None:
                continue
            if not held:
                if sc is not None and sc >= args.t_in:
                    held, entry_px, entry_d, entry_k, peak = True, px, d, k, sc
            else:
                if sc is not None:
                    peak = max(peak, sc)
                gross = (px / entry_px - 1) * 100
                hold_days = (_d(d) - _d(entry_d)).days
                exit_now = (sc is None or sc < args.t_out
                            or (peak - (sc if sc is not None else 0)) >= args.trail
                            or hold_days >= args.max_hold or gross <= args.stop)
                if exit_now:
                    trades.append((sym, entry_d, d, gross, gross - args.cost, hold_days))
                    for j in range(entry_k, k):
                        held_by_interval[j].add(sym)
                    held = False
        if held:                                       # close open position at last date
            d = cdates[-1]; px = price(sym, d)
            if px is not None:
                gross = (px / entry_px - 1) * 100
                trades.append((sym, entry_d, d, gross, gross - args.cost, (_d(d) - _d(entry_d)).days))
                for j in range(entry_k, len(cdates) - 1):
                    held_by_interval[j].add(sym)

    # --- per-trade economics (net of cost) ----------------------------------
    if not trades:
        print("NO TRADES — loosen --t-in or extend --lookback."); conn.close(); return 0
    nets = [t[4] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    holds = [t[5] for t in trades]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
    # benchmark: NIFTYBEES return over each trade's holding window
    nb_s, _nbd = edge_stats.load_series(conn, "NIFTYBEES")
    def nb(d):
        return nb_s.get(d)
    exc = []
    for sym, di, do, gross, net, _hd in trades:
        b0, b1 = nb(di), nb(do)
        if b0 and b1:
            exc.append(net - (b1 / b0 - 1) * 100)
    print("================ PER-TRADE (net of cost, A+B tier) ================")
    print(f"  trades={len(trades)}  win_rate={100*len(wins)/len(trades):.1f}%  "
          f"expectancy={sum(nets)/len(nets):+.2f}%/trade  median={sorted(nets)[len(nets)//2]:+.2f}%")
    print(f"  avg_win={sum(wins)/len(wins):+.2f}%  avg_loss={(sum(losses)/len(losses) if losses else 0):+.2f}%  "
          f"profit_factor={pf:.2f}")
    print(f"  avg_hold={sum(holds)/len(holds):.0f}d  (range {min(holds)}-{max(holds)}d)")
    if exc:
        beat = sum(1 for e in exc if e > 0)
        print(f"  vs holding NIFTYBEES same days: excess={sum(exc)/len(exc):+.2f}%/trade  "
              f"beat-market={100*beat/len(exc):.1f}% of trades")

    # --- equal-weight portfolio equity curve vs buy-and-hold market ---------
    eq = 1.0; curve = [1.0]; peak_eq = 1.0; maxdd = 0.0; rets = []
    for k in range(len(cdates) - 1):
        holders = held_by_interval[k]
        r = 0.0
        if holders:
            prs = []
            for sym in holders:
                p0, p1 = price(sym, cdates[k]), price(sym, cdates[k + 1])
                if p0 and p1:
                    prs.append(p1 / p0 - 1)
            if prs:
                r = sum(prs) / len(prs)
        rets.append(r)
        eq *= (1 + r); curve.append(eq)
        peak_eq = max(peak_eq, eq); maxdd = min(maxdd, eq / peak_eq - 1)
    yrs = (_d(cdates[-1]) - _d(cdates[0])).days / 365.25
    ppy = len(rets) / yrs if yrs else 0
    import statistics as st
    sharpe = (st.mean(rets) / st.pstdev(rets) * (ppy ** 0.5)) if len(rets) > 1 and st.pstdev(rets) else 0
    nb0, nb1 = nb(cdates[0]), nb(cdates[-1])
    mkt = (nb1 / nb0 - 1) * 100 if (nb0 and nb1) else 0
    print("\n================ PORTFOLIO (equal-weight, cost in per-trade) ================")
    print(f"  strategy total={100*(eq-1):+.1f}%  CAGR={100*(eq**(1/yrs)-1):+.1f}%  "
          f"Sharpe={sharpe:.2f}  maxDD={100*maxdd:.1f}%  over {yrs:.1f}y")
    print(f"  buy&hold NIFTYBEES total={mkt:+.1f}%  CAGR={100*((1+mkt/100)**(1/yrs)-1):+.1f}%")
    print("\n(Note: weekly cadence — finer would catch faster exits. Q+V is a slow score; "
          "the dynamic exit matters more once fast factors are added.)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
