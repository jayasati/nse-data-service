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

from datetime import date, datetime, timezone                     # noqa: E402

from nse_data.backtester._core import cpcv, metrics, registry      # noqa: E402
from nse_data.backtester._core.runner import (                     # noqa: E402
    commit_report, run_backtest_for_universe,
)
from nse_data.backtester.strategies.registry import STRATEGIES, resolve  # noqa: E402
from nse_data.indicators.universe import fno_plus_nifty500         # noqa: E402
from nse_data.storage.db import open_db                            # noqa: E402

# Strategies whose bars come from intraday candles vs daily bhavcopy — used only
# to pick where the available date range is read from.
_INTRADAY = {"bb_ema9_30m", "orb_vwap"}


def _data_range(conn, strategy: str) -> tuple[date, date] | None:
    if strategy in _INTRADAY:
        row = conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM raw_intraday_candles WHERE interval='minute'"
        ).fetchone()
        if not row or row[0] is None:
            return None
        to_d = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).date()
        return to_d(row[0]), to_d(row[1])
    row = conn.execute("SELECT MIN(date), MAX(date) FROM raw_bhavcopy_cm").fetchone()
    if not row or row[0] is None:
        return None
    return date.fromisoformat(row[0][:10]), date.fromisoformat(row[1][:10])


def _verdict(net_sharpe: float) -> str:
    if net_sharpe > 0.5:
        return "PROMOTE candidate (net Sharpe > 0.5 — confirm with CPCV)"
    if net_sharpe < 0:
        return "SHELVE (net Sharpe < 0 — no edge after costs)"
    return "NEEDS WORK (0 ≤ net Sharpe ≤ 0.5)"


def evaluate(conn, name, symbols, *, progress_every, commit, db_label,
             run_cpcv=False, register=False, folds=10, run_date=""):
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

    cpcv_res, date_range = None, "full"
    if run_cpcv:
        rng = _data_range(conn, name)
        if rng:
            date_range = f"{rng[0]}..{rng[1]}"
            print(f"--- CPCV {folds} folds over {date_range} …", flush=True)
            cpcv_res = cpcv.run_cpcv(
                conn, symbols, cfg=cfg, start=rng[0], end=rng[1], n_folds=folds,
                on_fold=lambda i, s, e, d: print(
                    f"   fold {i}: {s}..{e}  trades={d['trades']}  net_sharpe={d['net_sharpe']}"),
            )
            print(f"CPCV avg net Sharpe={cpcv_res['avg_sharpe']} "
                  f"({cpcv_res['folds_positive']}/{cpcv_res['n_folds']} folds positive) "
                  f"-> {'PASS' if cpcv_res['passes'] else 'FAIL'}")

    if commit:
        run_id = commit_report(
            conn, report, cfg=cfg, universe=db_label,
            symbols_count=len(symbols), start_date="", end_date="",
            notes="phase3_eval cost-adjusted run",
        )
        print(f"committed run_id={run_id}")

    if register:
        rid = registry.record_run(
            conn, run_date=run_date, strategy_name=name, cfg=cfg,
            date_range=date_range, metrics=m,
            cpcv_avg_sharpe=cpcv_res["avg_sharpe"] if cpcv_res else None,
            cpcv_folds_pos=cpcv_res["folds_positive"] if cpcv_res else None,
            notes=f"phase3_eval; {len(symbols)} symbols",
        )
        verdict = conn.execute(
            "SELECT verdict FROM backtest_registry WHERE id=?", (rid,)).fetchone()[0]
        print(f"registry id={rid} verdict={verdict}")


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
    p.add_argument("--cpcv", action="store_true",
                   help="also run fold-wise temporal CV (task 11.2)")
    p.add_argument("--folds", type=int, default=10, help="CPCV fold count")
    p.add_argument("--register", action="store_true",
                   help="record the run + verdict in backtest_registry (task 11.1/11.3)")
    args = p.parse_args(argv)
    run_date = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

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
                     commit=args.commit, db_label="fno_plus_nifty500",
                     run_cpcv=args.cpcv, register=args.register,
                     folds=args.folds, run_date=run_date)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
