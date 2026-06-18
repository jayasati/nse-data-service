"""Survivorship-corrected E2 backtest. The plain strategy backtests trade today's
A+B universe over a PAST window — implicit look-ahead (we only "chose" names that
turned out liquid/alive). Here the eligible universe is rebuilt POINT-IN-TIME at
each cadence date from TRAILING candles only (the same liquidity+volatility rule
build_tradeable_universe uses: ≥200 of trailing-252 traded days, median turnover
≥₹5cr, annualised vol <50%) — so we only ever hold names that were tradeable as of
that date. Scores are also computed within that PIT-eligible set, so the
cross-sectional rank is honest too.

Residual caveat: names fully absent from our candle set (true delistings) still
can't be traded — this corrects look-ahead UNIVERSE MEMBERSHIP, not delisting gaps.

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_strategy_pit.py --engines quality,valuation
"""
from __future__ import annotations

import argparse
import bisect
import datetime as _dt
import importlib
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ENGINE_MODS = {
    "quality": "nse_data.research.quality_engine",
    "valuation": "nse_data.research.valuation_engine",
    "momentum": "nse_data.research.momentum_engine",
    "ownership": "nse_data.research.ownership_engine",
}
WIN, MIN_DAYS, MIN_TURN_CR, MAX_VOL = 252, 200, 5.0, 50.0


def _d(s):
    return _dt.date.fromisoformat(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--engines", default="quality,valuation")
    ap.add_argument("--cadence", type=int, default=5)
    ap.add_argument("--lookback", type=int, default=520)
    ap.add_argument("--t-in", type=float, default=80.0)
    ap.add_argument("--t-out", type=float, default=60.0)
    ap.add_argument("--trail", type=float, default=15.0)
    ap.add_argument("--max-hold", type=int, default=120)
    ap.add_argument("--stop", type=float, default=-15.0)
    ap.add_argument("--cost", type=float, default=1.0)
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats
    from nse_data.fundamentals.sectors import sector_class_for
    engs = [importlib.import_module(ENGINE_MODS[e]) for e in args.engines.split(",")]

    conn = open_db(args.db)
    sec_cache: dict = {}
    def sector_of(s):
        if s not in sec_cache:
            sec_cache[s] = sector_class_for(s).value
        return sec_cache[s]

    # all symbols with daily candles → per-symbol arrays (chronological)
    print("loading candles for point-in-time eligibility...", flush=True)
    cand: dict = {}
    syms_all = [s for (s,) in conn.execute(
        "SELECT DISTINCT symbol FROM raw_intraday_candles WHERE interval='day'")]
    for sym in syms_all:
        rows = conn.execute(
            "SELECT date(ts,'unixepoch','+05:30') d, close, volume FROM raw_intraday_candles "
            "WHERE symbol=? AND interval='day' ORDER BY ts", (sym,)).fetchall()
        dts = [r[0] for r in rows]
        cls = [r[1] for r in rows]
        tov = [(r[1] * r[2] / 1e7) if (r[1] and r[2]) else 0.0 for r in rows]
        if len(dts) >= 30:
            cand[sym] = (dts, cls, tov)

    cal = [r[0] for r in conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d")][-args.lookback:]
    cdates = cal[::args.cadence]
    eps = {d: ep for d, ep in conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d")}

    def eligible_at(date: str) -> set:
        out = set()
        for sym, (dts, cls, tov) in cand.items():
            j = bisect.bisect_right(dts, date) - 1
            if j < MIN_DAYS - 1:
                continue
            lo = max(0, j - WIN + 1)
            wt = tov[lo:j + 1]
            if len(wt) < MIN_DAYS or st.median(wt) < MIN_TURN_CR:
                continue
            wc = cls[lo:j + 1]
            rets = [wc[i] / wc[i - 1] - 1 for i in range(1, len(wc)) if wc[i - 1]]
            if len(rets) > 5 and st.pstdev(rets) * (252 ** 0.5) * 100 >= MAX_VOL:
                continue
            out.add(sym)
        return out

    elig = {d: eligible_at(d) for d in cdates}
    sizes = [len(elig[d]) for d in cdates]
    print(f"engines={args.engines}  PIT-eligible universe: {min(sizes)}–{max(sizes)} names/date "
          f"(today's A+B for comparison is fixed)  score-dates={len(cdates)} "
          f"({cdates[0]}..{cdates[-1]})\n", flush=True)

    price_cache: dict = {}
    def price_of(sym, d):
        if sym not in price_cache:
            price_cache[sym] = edge_stats.load_series(conn, sym)[0]
        return price_cache[sym].get(d)
    nb_series = edge_stats.load_series(conn, "NIFTYBEES")[0]

    # score each date over the PIT-eligible set
    scores: dict = {s: {} for s in cand}
    for k, d in enumerate(cdates, 1):
        elist = list(elig[d])
        per = [m.score_universe(conn, elist, eps[d], sector_of) for m in engs]
        for sym in elist:
            vals = [p[sym]["score"] for p in per if sym in p]
            if vals:
                scores[sym][d] = sum(vals) / len(vals)
        if k % 15 == 0:
            print(f"  scored {k}/{len(cdates)}", flush=True)

    # state machine — entry requires PIT-eligibility at that date
    trades = []
    n_int = len(cdates)
    holders = [set() for _ in range(n_int)]
    for sym in cand:
        held = False
        entry_px, entry_d, entry_k, peak = 0.0, "", 0, 0.0
        for k, d in enumerate(cdates):
            sc = scores[sym].get(d)
            px = price_of(sym, d)
            if px is None:
                continue
            if not held:
                if sc is not None and sc >= args.t_in and sym in elig[d]:
                    held, entry_px, entry_d, entry_k, peak = True, px, d, k, sc
            else:
                if sc is not None:
                    peak = max(peak, sc)
                gross = (px / entry_px - 1) * 100
                hd = (_d(d) - _d(entry_d)).days
                if (sc is None or sc < args.t_out or (peak - (sc if sc is not None else 0)) >= args.trail
                        or hd >= args.max_hold or gross <= args.stop):
                    trades.append((entry_d, d, gross - args.cost))
                    for j in range(entry_k, k):
                        holders[j].add(sym)
                    held = False
        if held:
            d = cdates[-1]; px = price_of(sym, d)
            if px is not None:
                trades.append((entry_d, d, (px / entry_px - 1) * 100 - args.cost))
                for j in range(entry_k, n_int - 1):
                    holders[j].add(sym)

    if not trades:
        print("NO TRADES."); conn.close(); return 0
    nets = [t[2] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) else float("inf")
    irets = []
    for k in range(n_int - 1):
        prs = [price_of(s, cdates[k + 1]) / price_of(s, cdates[k]) - 1
               for s in holders[k] if price_of(s, cdates[k]) and price_of(s, cdates[k + 1])]
        irets.append(sum(prs) / len(prs) if prs else 0.0)
    yrs = (_d(cdates[-1]) - _d(cdates[0])).days / 365.25
    eq = 1.0
    for r in irets:
        eq *= (1 + r)
    ppy = len(irets) / yrs if yrs else 0
    sharpe = (st.mean(irets) / st.pstdev(irets) * (ppy ** 0.5)) if len(irets) > 1 and st.pstdev(irets) else 0
    nb0, nb1 = nb_series.get(cdates[0]), nb_series.get(cdates[-1])
    mkt = (100 * ((nb1 / nb0) ** (1 / yrs) - 1)) if (nb0 and nb1 and yrs) else 0

    print("============ SURVIVORSHIP-CORRECTED (point-in-time universe) ============")
    print(f"  trades={len(trades)}  win={100*len(wins)/len(trades):.0f}%  "
          f"expectancy={sum(nets)/len(nets):+.2f}%/trade  PF={pf:.2f}")
    print(f"  portfolio CAGR={100*(eq**(1/yrs)-1):+.1f}%  Sharpe={sharpe:.2f}  "
          f"total={100*(eq-1):+.1f}%  | market CAGR={mkt:+.1f}%")
    n = n_int - 1
    print("  sub-periods:")
    for label, lo, hi in [("P1", 0, n//3), ("P2", n//3, 2*n//3), ("P3", 2*n//3, n)]:
        e = 1.0
        for x in irets[lo:hi]:
            e *= (1 + x)
        b0, b1 = nb_series.get(cdates[lo]), nb_series.get(cdates[hi])
        m = (b1/b0-1)*100 if (b0 and b1) else 0
        print(f"    {label} {cdates[lo]}..{cdates[hi]}: strat={100*(e-1):+.1f}%  market={m:+.1f}%")
    print("\n(Compare CAGR to the non-PIT run — the gap is the survivorship inflation.)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
