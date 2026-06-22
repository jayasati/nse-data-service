#!/usr/bin/env python3
"""Net-of-cost validation of the intraday ORB+VWAP setup over the full intraday history.

Runs the canonical ORB+VWAP engine (break OR-high + close>VWAP, ATR stop, RR target, EOD exit)
across a liquid universe and every available session, then applies a realistic Indian intraday
(MIS equity) cost model. Reports gross vs net expectancy, win rate, profit factor, and avg-R at
several cost levels — the only honest gate before trusting any intraday setup with money.

    PYTHONPATH=src python scripts/backtest_intraday.py [n_symbols]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nse_data.backtester.strategies.orb_vwap.config import OrbVwapConfig  # noqa: E402
from nse_data.backtester.strategies.orb_vwap.engine import run_backtest_for_symbol  # noqa: E402
from nse_data.indicators.universe import top_fno_by_value  # noqa: E402

# Round-trip all-in cost (% of trade value) for intraday MIS equity on liquid NSE names.
# ~0.11% is pure charges (brokerage 0.06 + STT 0.025 + exch/sebi/stamp + GST); the rest is slippage.
COST_LEVELS = {"charges only 0.12%": 0.12, "+ light slippage 0.18%": 0.18,
               "+ realistic slippage 0.25%": 0.25}


def main() -> int:
    n_syms = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    conn = sqlite3.connect(str(ROOT / "data/nse.db"), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    symbols = top_fno_by_value(conn, n_syms)
    cfg = OrbVwapConfig()

    rets = []   # per-trade gross return fraction (exit-entry)/entry, signed by direction (long)
    rmults = []
    for sym in symbols:
        try:
            _, trades = run_backtest_for_symbol(conn, sym, cfg=cfg)
        except Exception:
            continue
        for t in trades:
            if not t.entry_price:
                continue
            g = (t.exit_price - t.entry_price) / t.entry_price        # long-only
            risk = (t.entry_price - t.sl) / t.entry_price
            rets.append(g)
            rmults.append(g / risk if risk > 0 else 0.0)

    n = len(rets)
    if n == 0:
        print("no trades"); return 1
    gross_avg = sum(rets) / n * 100
    print(f"ORB+VWAP intraday — {len(symbols)} liquid F&O names, {n} trades "
          f"(~{n / len(symbols):.0f}/symbol over ~250 sessions)\n")
    print(f"{'cost level':28} {'win%':>6} {'avg/trade':>10} {'expectancy(R)':>13} "
          f"{'profit factor':>13} {'total net':>10}")
    print(f"{'GROSS (no cost)':28} {sum(r > 0 for r in rets) / n * 100:>5.1f}% "
          f"{gross_avg:>9.3f}% {sum(rmults) / n:>12.3f} "
          f"{_pf(rets):>13.2f} {sum(rets) * 100:>9.1f}%")
    for label, c in COST_LEVELS.items():
        net = [r - c / 100 for r in rets]                            # subtract round-trip cost
        # expectancy in R after cost: convert the cost to R using each trade's own risk
        net_r = []
        for i, r in enumerate(rets):
            risk = r / rmults[i] if rmults[i] else None
            net_r.append((r - c / 100) / risk if risk and risk > 0 else 0.0)
        wr = sum(x > 0 for x in net) / n * 100
        print(f"{label:28} {wr:>5.1f}% {sum(net) / n * 100:>9.3f}% "
              f"{sum(net_r) / n:>12.3f} {_pf(net):>13.2f} {sum(net) * 100:>9.1f}%")
    return 0


def _pf(rets) -> float:
    g = sum(r for r in rets if r > 0)
    loss = -sum(r for r in rets if r < 0)
    return g / loss if loss > 0 else float("inf")


if __name__ == "__main__":
    raise SystemExit(main())
