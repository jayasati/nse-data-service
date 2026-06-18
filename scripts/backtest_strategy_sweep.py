"""Robustness harness for the E2 path-dependent strategy. Scores the universe
ONCE at a cadence (the expensive part), then cheaply re-runs the buy/hold/
sell-on-decline simulation across:
  #1 a PARAMETER GRID (T_in / trail) — is the edge structural or curve-fit?
  #2 SUB-PERIODS — does it profit in each chunk, or one lucky stretch?
Pass --engines to average several engines into the score path (re-test the
E1-failed fast factors under E2 → does the dynamic exit extract value E1 missed?).

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_strategy_sweep.py \
        --engines quality,valuation
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GRADES = ("A core", "B tradeable")
ENGINE_MODS = {
    "quality": "nse_data.research.quality_engine",
    "valuation": "nse_data.research.valuation_engine",
    "momentum": "nse_data.research.momentum_engine",
    "ownership": "nse_data.research.ownership_engine",
    "turnaround": "nse_data.research.turnaround_engine",
}


def _d(s):
    return _dt.date.fromisoformat(s)


def simulate(syms, cdates, scores, price_of, nb_of, t_in, t_out, trail, max_hold, stop, cost):
    """Run the state machine + equal-weight portfolio. Returns a stats dict."""
    trades = []                                        # (entry_date, exit_date, net%)
    n_int = len(cdates)
    holders = [set() for _ in range(n_int)]
    for sym in syms:
        held = False
        entry_px, entry_d, entry_k, peak = 0.0, "", 0, 0.0
        for k, d in enumerate(cdates):
            sc = scores[sym].get(d)
            px = price_of(sym, d)
            if px is None:
                continue
            if not held:
                if sc is not None and sc >= t_in:
                    held, entry_px, entry_d, entry_k, peak = True, px, d, k, sc
            else:
                if sc is not None:
                    peak = max(peak, sc)
                gross = (px / entry_px - 1) * 100
                hd = (_d(d) - _d(entry_d)).days
                if (sc is None or sc < t_out or (peak - (sc if sc is not None else 0)) >= trail
                        or hd >= max_hold or gross <= stop):
                    trades.append((entry_d, d, gross - cost))
                    for j in range(entry_k, k):
                        holders[j].add(sym)
                    held = False
        if held:
            d = cdates[-1]; px = price_of(sym, d)
            if px is not None:
                trades.append((entry_d, d, (px / entry_px - 1) * 100 - cost))
                for j in range(entry_k, n_int - 1):
                    holders[j].add(sym)
    # portfolio interval returns (equal-weight across concurrent holders)
    irets = []
    for k in range(n_int - 1):
        prs = [price_of(s, cdates[k + 1]) / price_of(s, cdates[k]) - 1
               for s in holders[k] if price_of(s, cdates[k]) and price_of(s, cdates[k + 1])]
        irets.append(sum(prs) / len(prs) if prs else 0.0)
    nets = [t[2] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    eq = 1.0
    for r in irets:
        eq *= (1 + r)
    return {"trades": trades, "n": len(trades), "irets": irets,
            "win_rate": 100 * len(wins) / len(trades) if trades else 0,
            "expectancy": sum(nets) / len(nets) if nets else 0,
            "pf": (sum(wins) / abs(sum(losses))) if losses and sum(losses) else float("inf"),
            "total": 100 * (eq - 1)}


def cagr_sharpe(irets, cdates):
    yrs = (_d(cdates[-1]) - _d(cdates[0])).days / 365.25
    eq = 1.0
    for r in irets:
        eq *= (1 + r)
    ppy = len(irets) / yrs if yrs else 0
    sharpe = (st.mean(irets) / st.pstdev(irets) * (ppy ** 0.5)) if len(irets) > 1 and st.pstdev(irets) else 0
    return (100 * (eq ** (1 / yrs) - 1) if yrs else 0), sharpe, yrs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--engines", default="quality,valuation")
    ap.add_argument("--cadence", type=int, default=5)
    ap.add_argument("--lookback", type=int, default=520)
    ap.add_argument("--max-hold", type=int, default=120)
    ap.add_argument("--stop", type=float, default=-15.0)
    ap.add_argument("--cost", type=float, default=1.0)
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats
    from nse_data.fundamentals.sectors import sector_class_for
    engs = [importlib.import_module(ENGINE_MODS[e]) for e in args.engines.split(",")]

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
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d")][-args.lookback:]
    cdates = cal[::args.cadence]
    eps = {d: ep for d, ep in conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d")}
    print(f"engines={args.engines}  universe={len(syms)}  score-dates={len(cdates)} "
          f"({cdates[0]}..{cdates[-1]})\n", flush=True)

    sc_cache: dict = {}
    def price_of(sym, d):
        if sym not in sc_cache:
            sc_cache[sym] = edge_stats.load_series(conn, sym)[0]
        return sc_cache[sym].get(d)
    nb_series = edge_stats.load_series(conn, "NIFTYBEES")[0]
    def nb_of(d):
        return nb_series.get(d)

    # score once: average the engines' [0,100] scores per symbol per date
    scores: dict = {s: {} for s in syms}
    for k, d in enumerate(cdates, 1):
        per = [m.score_universe(conn, syms, eps[d], sector_of) for m in engs]
        for sym in syms:
            vals = [p[sym]["score"] for p in per if sym in p]
            if vals:
                scores[sym][d] = sum(vals) / len(vals)
        if k % 15 == 0:
            print(f"  scored {k}/{len(cdates)}", flush=True)

    base = dict(t_in=80, t_out=60, trail=15, max_hold=args.max_hold, stop=args.stop, cost=args.cost)
    r0 = simulate(syms, cdates, scores, price_of, nb_of, **base)
    c0, s0, yrs = cagr_sharpe(r0["irets"], cdates)
    nb0, nb1 = nb_of(cdates[0]), nb_of(cdates[-1])
    mkt_cagr = (100 * (((nb1 / nb0)) ** (1 / yrs) - 1)) if (nb0 and nb1 and yrs) else 0
    print(f"BASE (in80/out60/trail15): trades={r0['n']} win={r0['win_rate']:.0f}% "
          f"exp={r0['expectancy']:+.2f}%/trade PF={r0['pf']:.2f}  "
          f"port CAGR={c0:+.1f}% Sharpe={s0:.2f}  | market CAGR={mkt_cagr:+.1f}%\n")

    # ---- #1 PARAMETER GRID ------------------------------------------------
    print("#1 PARAMETER ROBUSTNESS (CAGR% / PF / expectancy% per trade)")
    print(f"{'':8}" + "".join(f"trail{t:<8}" for t in (10, 15, 20, 25)))
    for t_in in (72, 76, 80, 84):
        row = f"in{t_in:<6}"
        for trail in (10, 15, 20, 25):
            r = simulate(syms, cdates, scores, price_of, nb_of,
                         t_in=t_in, t_out=t_in - 20, trail=trail,
                         max_hold=args.max_hold, stop=args.stop, cost=args.cost)
            c, _s, _y = cagr_sharpe(r["irets"], cdates)
            row += f"{c:+.0f}/{r['pf']:.1f}/{r['expectancy']:+.1f} "
        print(row, flush=True)

    # ---- #2 SUB-PERIODS (base params) -------------------------------------
    print("\n#2 SUB-PERIOD ROBUSTNESS (base params, 3 chunks)")
    n = len(cdates) - 1
    for label, lo, hi in [("P1", 0, n // 3), ("P2", n // 3, 2 * n // 3), ("P3", 2 * n // 3, n)]:
        seg = r0["irets"][lo:hi]
        eq = 1.0
        for x in seg:
            eq *= (1 + x)
        d0, d1 = cdates[lo], cdates[hi]
        b0, b1 = nb_of(d0), nb_of(d1)
        mkt = (b1 / b0 - 1) * 100 if (b0 and b1) else 0
        print(f"  {label} {d0}..{d1}: strategy={100*(eq-1):+.1f}%  market={mkt:+.1f}%  "
              f"{'BEAT' if 100*(eq-1) > mkt else 'lag '}", flush=True)
    print("\n(Robust = positive across most of the grid AND every sub-period. "
          "Survivorship still uncorrected — discount absolute CAGR.)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
