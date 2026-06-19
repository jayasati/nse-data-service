"""Capital portfolio simulation of the Buy-Score dynamic strategy — a real ₹-blotter,
not a %-equity curve. Starts from a fixed capital, runs an N-slot equal-weight book
through the validated buy-high / sell-on-decline state machine on the factor_snapshot
Buy Score (the same trend-aware score the chart shows), and prints every trade's net
P&L after costs AND short-term capital-gains tax.

    PYTHONPATH=src .venv/bin/python -u scripts/portfolio_sim.py --capital 100000 --slots 5

Costs: round-trip `--cost`% split across buy+sell. Tax: STCG (sec 111A, ≤12m holdings —
all trades here are short-term) at `--stcg`% on NET realised gains (losses offset gains),
which is how Indian equity tax actually works; the per-trade column shows the gross and a
per-trade STCG estimate for visibility.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _d(s):
    return _dt.date.fromisoformat(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--slots", type=int, default=5, help="max concurrent positions")
    ap.add_argument("--t-in", type=float, default=80.0)
    ap.add_argument("--t-out", type=float, default=60.0)
    ap.add_argument("--trail", type=float, default=15.0)
    ap.add_argument("--max-hold", type=int, default=120)
    ap.add_argument("--stop", type=float, default=-15.0)
    ap.add_argument("--cost", type=float, default=1.0, help="round-trip cost %%")
    ap.add_argument("--stcg", type=float, default=20.0, help="short-term cap-gains tax %%")
    ap.add_argument("--symbol", default=None, help="restrict to a single symbol")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import buy_score as bs
    conn = open_db(args.db)
    W = bs.REGIME_WEIGHTS["neutral"]
    half = args.cost / 200.0                                  # per-side cost fraction

    # Buy Score per (date, symbol) from the stored factors
    keys = ["quality", "valuation", "momentum", "surprise", "catalyst", "turnaround", "liquidity", "risk"]
    by_date: dict[str, dict] = {}
    for row in conn.execute(
            "SELECT snapshot_date, symbol, quality, valuation, momentum, surprise, catalyst, "
            "turnaround, liquidity, risk FROM factor_snapshot"):
        d, sym = row[0], row[1]
        if args.symbol and sym != args.symbol.upper():
            continue
        buy, _ = bs.buy_raw(dict(zip(keys, row[2:])), W)
        if buy is not None:
            by_date.setdefault(d, {})[sym] = buy
    dates = sorted(by_date)
    if len(dates) < 30:
        print(f"only {len(dates)} snapshot dates — run the factor_snapshot backfill first.")
        return 1

    # lazy daily price cache  {sym: {date: close}}
    _px: dict[str, dict] = {}
    def price(sym, d):
        if sym not in _px:
            _px[sym] = {dd: c for dd, c in conn.execute(
                "SELECT date(ts,'unixepoch','+05:30'), close FROM raw_intraday_candles "
                "WHERE symbol=? AND interval='day' AND close IS NOT NULL", (sym,))}
        return _px[sym].get(d)

    cash = args.capital
    pos: dict[str, dict] = {}                                 # sym -> {qty, entry_px, entry_d, peak, invested}
    trades = []
    for d in dates:
        sc_today = by_date[d]
        # ---- exits ----
        for sym in list(pos):
            sc = sc_today.get(sym)
            px = price(sym, d)
            if px is None:
                continue
            p = pos[sym]
            if sc is not None:
                p["peak"] = max(p["peak"], sc)
            gross_pct = (px / p["entry_px"] - 1) * 100
            hd = (_d(d) - _d(p["entry_d"])).days
            if (sc is None or sc < args.t_out or (p["peak"] - (sc if sc is not None else 0)) >= args.trail
                    or hd >= args.max_hold or gross_pct <= args.stop):
                proceeds = p["qty"] * px * (1 - half)
                pnl = proceeds - p["invested"]
                cash += proceeds
                trades.append({"sym": sym, "in_d": p["entry_d"], "out_d": d, "days": hd,
                               "qty": p["qty"], "in_px": p["entry_px"], "out_px": px,
                               "invested": p["invested"], "pnl": pnl})
                del pos[sym]
        # ---- entries (best Buy Scores ≥ t_in, fill free slots) ----
        free = args.slots - len(pos)
        if free > 0 and cash > 1:
            cands = sorted(((s, v) for s, v in sc_today.items()
                            if v >= args.t_in and s not in pos and price(s, d)), key=lambda kv: -kv[1])
            for sym, _v in cands[:free]:
                alloc = cash / (args.slots - len(pos))       # equal-split remaining cash
                px = price(sym, d)
                qty = int(alloc / (px * (1 + half)))
                if qty <= 0:
                    continue
                invested = qty * px * (1 + half)
                cash -= invested
                pos[sym] = {"qty": qty, "entry_px": px, "entry_d": d, "peak": sc_today[sym],
                            "invested": invested}
    # close any open positions at the last date
    lastd = dates[-1]
    for sym, p in list(pos.items()):
        px = price(sym, lastd)
        if px is None:
            continue
        proceeds = p["qty"] * px * (1 - half)
        cash += proceeds
        trades.append({"sym": sym, "in_d": p["entry_d"], "out_d": lastd,
                       "days": (_d(lastd) - _d(p["entry_d"])).days, "qty": p["qty"],
                       "in_px": p["entry_px"], "out_px": px, "invested": p["invested"],
                       "pnl": proceeds - p["invested"], "open": True})

    # ---- blotter ----
    print(f"\n=== ₹{args.capital:,.0f} PORTFOLIO SIM — Buy Score (t_in {args.t_in}/t_out {args.t_out}/"
          f"trail {args.trail}), {args.slots} slots, cost {args.cost}% rt, STCG {args.stcg}% ===")
    print(f"period {dates[0]} → {dates[-1]}   ({len(trades)} trades)\n")
    print(f"{'#':>3} {'symbol':<11}{'entry':<11}{'exit':<11}{'d':>4} {'qty':>5} "
          f"{'in₹':>8} {'out₹':>8} {'gross P&L₹':>11} {'tax₹':>8} {'net₹':>10}")
    gross_sum = 0.0
    for i, t in enumerate(sorted(trades, key=lambda x: x["in_d"]), 1):
        tax = max(0.0, t["pnl"]) * args.stcg / 100.0         # per-trade STCG estimate
        net = t["pnl"] - tax
        gross_sum += t["pnl"]
        flag = "*" if t.get("open") else ""
        print(f"{i:>3} {t['sym']:<11}{t['in_d']:<11}{t['out_d']:<11}{t['days']:>4} {t['qty']:>5} "
              f"{t['in_px']:>8.1f} {t['out_px']:>8.1f} {t['pnl']:>+11,.0f} {tax:>8,.0f} {net:>+10,.0f}{flag}")

    wins = [t for t in trades if t["pnl"] > 0]
    # realistic portfolio tax: STCG on NET realised gains (losses offset, all short-term)
    net_tax = max(0.0, gross_sum) * args.stcg / 100.0
    net_after_tax = gross_sum - net_tax
    final = args.capital + net_after_tax
    print(f"\n--- SUMMARY ---")
    print(f"  trades={len(trades)}  win={100*len(wins)/len(trades):.0f}%  "
          f"avg/trade=₹{gross_sum/len(trades):+,.0f}")
    print(f"  gross profit (after trading costs):  ₹{gross_sum:+,.0f}")
    print(f"  STCG tax @ {args.stcg}% (on net gains): ₹{net_tax:,.0f}")
    print(f"  NET PROFIT AFTER TAX:                ₹{net_after_tax:+,.0f}")
    print(f"  final value:  ₹{final:,.0f}   (from ₹{args.capital:,.0f}  →  {100*net_after_tax/args.capital:+.1f}%)")
    yrs = (_d(dates[-1]) - _d(dates[0])).days / 365.25
    if yrs > 0 and final > 0:
        print(f"  CAGR after tax: {100*((final/args.capital)**(1/yrs)-1):+.1f}%   (over {yrs:.2f} yrs)")
    # NIFTYBEES buy-hold benchmark on the same capital
    nb = sorted((dd, c) for dd, c in conn.execute(
        "SELECT date(ts,'unixepoch','+05:30'), close FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' AND close IS NOT NULL"))
    def _nb_on(d):                                            # latest NIFTYBEES close on/before d
        prev = None
        for dd, c in nb:
            if dd <= d:
                prev = c
            else:
                break
        return prev
    n0, n1 = _nb_on(dates[0]), _nb_on(dates[-1])
    if n0 and n1:
        print(f"  vs NIFTYBEES buy&hold: ₹{args.capital*n1/n0:,.0f}  ({100*(n1/n0-1):+.1f}%)")
    print("  (* = position still open at period end, marked-to-market)\n")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
