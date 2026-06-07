"""
Phase 3 cost-adjusted backtest evaluation (Week 10, tasks 10.4/10.5/10.6).

Runs a strategy through the net-of-cost backtester and prints gross-vs-net
metrics. Progress is logged live (one line every N symbols) so a long intraday
run is watchable on your terminal — not a black box.

    # one strategy, full FNO+Nifty500 universe
    PYTHONPATH=src python scripts/phase3_eval.py --strategy macd_willr_daily

    # quick look on the first 100 names (good for the slow 30-min strategy)
    PYTHONPATH=src python scripts/phase3_eval.py --strategy bb_ema9_30m --limit 100

    # all three, and persist each run to backtest_runs
    PYTHONPATH=src python scripts/phase3_eval.py --strategy all --commit

Strategies: bb_ema9_30m, macd_willr_daily, breakout_52wh, all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nse_data.backtester._core import metrics                      # noqa: E402
from nse_data.backtester._core.runner import (                     # noqa: E402
    commit_report, run_backtest_for_universe,
)
from nse_data.backtester.strategies.registry import STRATEGIES, resolve  # noqa: E402
from nse_data.indicators.universe import fno_plus_nifty500         # noqa: E402
from nse_data.storage.db import open_db                            # noqa: E402


def _verdict(net_sharpe: float) -> str:
    if net_sharpe > 0.5:
        return "PROMOTE candidate (net Sharpe > 0.5 — confirm with CPCV)"
    if net_sharpe < 0:
        return "SHELVE (net Sharpe < 0 — no edge after costs)"
    return "NEEDS WORK (0 ≤ net Sharpe ≤ 0.5)"


def evaluate(conn, name, symbols, *, progress_every, commit, db_label):
    cfg = resolve(name).config_cls(strategy=name)
    print(f"\n>>> {name}: {len(symbols)} symbols …", flush=True)
    t0 = time.time()
    report = run_backtest_for_universe(
        conn, symbols, cfg=cfg, progress_every=progress_every,
    )
    elapsed = time.time() - t0

    if not report.trades:
        print(f"<<< {name}: no trades ({elapsed:.0f}s)", flush=True)
        return

    m = metrics.summarize(report.trades, capital=cfg.notional_per_trade)
    print(f"<<< {name}: {report.total_trades} trades in {elapsed:.0f}s")
    print(json.dumps(m, indent=2))
    print(f"VERDICT: {_verdict(m['net_sharpe'])}")

    if commit:
        run_id = commit_report(
            conn, report, cfg=cfg, universe=db_label,
            symbols_count=len(symbols), start_date="", end_date="",
            notes="phase3_eval cost-adjusted run",
        )
        print(f"committed run_id={run_id}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 3 cost-adjusted backtest eval")
    p.add_argument("--strategy", default="all",
                   help="bb_ema9_30m | macd_willr_daily | breakout_52wh | all")
    p.add_argument("--db", default="data/nse.db")
    p.add_argument("--limit", type=int, default=0,
                   help="cap the universe to the first N symbols (0 = all)")
    p.add_argument("--progress-every", type=int, default=25,
                   help="log a progress line every N symbols (0 = silent)")
    p.add_argument("--commit", action="store_true",
                   help="persist each run to backtest_runs/backtest_trades")
    args = p.parse_args(argv)

    # Live progress to stdout from the runner's logger.
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format="%(message)s",
    )

    names = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    for n in names:
        if n not in STRATEGIES:
            print(f"unknown strategy {n!r}; known: {sorted(STRATEGIES)}")
            return 2

    conn = open_db(args.db)
    try:
        symbols = fno_plus_nifty500(conn)
        if args.limit:
            symbols = symbols[: args.limit]
        for n in names:
            evaluate(conn, n, symbols,
                     progress_every=args.progress_every,
                     commit=args.commit, db_label="fno_plus_nifty500")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
