#!/usr/bin/env python3
"""Run a strategy backtest over a symbol set and persist it for the UI (strategy_runs/trades).

    PYTHONPATH=src python scripts/strategy_backtest.py                 # default universe
    PYTHONPATH=src python scripts/strategy_backtest.py --symbols NIFTY RELIANCE TCS

The dashboard at /strategy reads whatever this writes — add a strategy by giving it a persist
adapter (see strategy/daily_sweep/persist.py) and pointing this at it.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nse_data.strategy.daily_sweep.config import DailySweepConfig  # noqa: E402
from nse_data.strategy.daily_sweep.persist import run_and_persist  # noqa: E402

_DEFAULT = ["NIFTY", "BANKNIFTY", "FINNIFTY", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
            "SBIN", "ITC", "LT", "AXISBANK", "KOTAKBANK", "BHARTIARTL", "BAJFINANCE", "HINDUNILVR",
            "MARUTI", "TATAMOTORS", "TATASTEEL", "SUNPHARMA", "ADANIENT", "ADANIPORTS", "WIPRO",
            "HCLTECH", "ULTRACEMCO", "ASIANPAINT", "TITAN", "NTPC", "ONGC"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--symbols", nargs="*", default=_DEFAULT)
    ap.add_argument("--notes", default=None)
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    run_id = run_and_persist(conn, args.symbols, DailySweepConfig(), notes=args.notes)
    n = conn.execute("SELECT n_trades FROM strategy_runs WHERE id=?", (run_id,)).fetchone()[0]
    print(f"persisted run #{run_id} — {n} trades over {len(args.symbols)} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
