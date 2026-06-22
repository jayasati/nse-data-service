#!/usr/bin/env python3
"""Does CONVICTION-style selection rescue the intraday ORB edge? (net-of-cost)

The broad ORB+VWAP test was net-negative because it trades every breakout. This tests the user's
hypothesis: take the intraday ORB setup ONLY on names that are directionally strong at the prior
close (a proxy for the conviction engine's validated-meaningful signals — daily structure + relative
strength), i.e. selection provides the edge and ORB is just the entry. Compares ALL trades vs the
SELECTED subset, net of a realistic Indian intraday cost model.

    PYTHONPATH=src python scripts/backtest_intraday_selective.py [n_symbols]
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nse_data.backtester.strategies.orb_vwap.config import OrbVwapConfig  # noqa: E402
from nse_data.backtester.strategies.orb_vwap.engine import run_backtest_for_symbol  # noqa: E402
from nse_data.indicators.universe import top_fno_by_value  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
COSTS = {"gross": 0.0, "charges 0.12%": 0.12, "+slippage 0.18%": 0.18}


def _daily_trend(conn, sym) -> dict:
    """date_iso -> True if, AS OF THAT DAY'S CLOSE, the name is in an uptrend
    (close > SMA20 and positive 10-day momentum). Used with the PRIOR day for a session."""
    rows = conn.execute("SELECT ts, close FROM raw_intraday_candles WHERE symbol=? AND "
                        "interval='day' ORDER BY ts", (sym,)).fetchall()
    if len(rows) < 25:
        return {}
    s = pd.Series([c for _, c in rows],
                  index=[dt.datetime.fromtimestamp(t, IST).date() for t, _ in rows])
    sma20 = s.rolling(20).mean()
    mom10 = s.pct_change(10)
    up = (s > sma20) & (mom10 > 0)
    return {d.isoformat(): bool(v) for d, v in up.items()}


def _stats(rets, rmults, cost):
    n = len(rets)
    if n == 0:
        return None
    net = [r - cost / 100 for r in rets]
    net_r = []
    for i, r in enumerate(rets):
        risk = r / rmults[i] if rmults[i] else None
        net_r.append((r - cost / 100) / risk if risk and risk > 0 else 0.0)
    g = sum(x for x in net if x > 0)
    loss = -sum(x for x in net if x < 0)
    pf = g / loss if loss > 0 else float("inf")
    return (n, sum(x > 0 for x in net) / n * 100, sum(net) / n * 100, sum(net_r) / n, pf)


def main() -> int:
    n_syms = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    conn = sqlite3.connect(str(ROOT / "data/nse.db"), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    symbols = top_fno_by_value(conn, n_syms)
    cfg = OrbVwapConfig()

    allt = {"r": [], "m": []}
    sel = {"r": [], "m": []}
    for sym in symbols:
        trend = _daily_trend(conn, sym)
        try:
            _, trades = run_backtest_for_symbol(conn, sym, cfg=cfg)
        except Exception:
            continue
        # sorted unique session dates to find the prior trading day
        dates = sorted(trend)
        for t in trades:
            if not t.entry_price:
                continue
            g = (t.exit_price - t.entry_price) / t.entry_price
            risk = (t.entry_price - t.sl) / t.entry_price
            m = g / risk if risk > 0 else 0.0
            allt["r"].append(g); allt["m"].append(m)
            sess = dt.datetime.fromtimestamp(t.entry_ts, IST).date().isoformat()
            # prior trading day's trend state (no lookahead)
            prior = [d for d in dates if d < sess]
            if prior and trend.get(prior[-1]):
                sel["r"].append(g); sel["m"].append(m)

    print(f"ORB+VWAP intraday, {len(symbols)} liquid F&O names\n")
    print(f"{'bucket / cost':30} {'trades':>7} {'win%':>6} {'avg':>8} {'exp(R)':>7} {'PF':>6}")
    for name, b in (("ALL breakouts", allt), ("SELECTED (prior-day uptrend)", sel)):
        for cl, c in COSTS.items():
            st = _stats(b["r"], b["m"], c)
            if st:
                nn, wr, avg, er, pf = st
                print(f"{name+' · '+cl:30} {nn:>7} {wr:>5.1f}% {avg:>7.3f}% {er:>6.2f} {pf:>6.2f}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
