"""
Temporal cross-validation for fixed-rule strategies (Week 11, task 11.2).

The checklist calls this CPCV. True CPCV (train/test folds) is for strategies
with *fitted* parameters — ours have fixed rules, so there's nothing to train.
The meaningful test is therefore **fold-wise out-of-sample consistency**: split
the history into N contiguous time folds (no shuffle, time order preserved) and
check whether the net-of-cost edge holds across folds — not just one lucky stretch.

Implementation note (matters): we run the backtest ONCE over the full history,
then bucket the resulting trades into folds by entry date. We do NOT re-run the
engine on each fold's date window — doing that would strip the lookback history
(52-week-high needs a year of prior bars; MACD needs warmup), starving most
folds of trades and producing a meaningless result. One full run also makes this
~N× faster.

Pass condition (task 11.2): average net Sharpe across folds is positive.

`split_folds` is pure and unit-tested; `run_cpcv` buckets a single backtest.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from . import metrics
from .runner import run_backtest_for_universe
from .types import StrategyConfig

_IST = timezone(timedelta(hours=5, minutes=30))


def split_folds(start: date, end: date, n_folds: int = 10) -> list[tuple[date, date]]:
    """Split [start, end] into `n_folds` contiguous (inclusive) date ranges."""
    if end < start:
        raise ValueError("end before start")
    total_days = (end - start).days + 1
    n_folds = max(1, min(n_folds, total_days))
    size = total_days / n_folds
    folds: list[tuple[date, date]] = []
    for i in range(n_folds):
        f_start = start + timedelta(days=int(round(i * size)))
        f_end = (start + timedelta(days=int(round((i + 1) * size)) - 1)
                 if i < n_folds - 1 else end)
        folds.append((f_start, f_end))
    return folds


def run_cpcv(
    conn: sqlite3.Connection,
    symbols: Iterable[str],
    *,
    cfg: StrategyConfig,
    start: date,
    end: date,
    n_folds: int = 10,
    on_fold=None,
) -> dict:
    """One full backtest, trades bucketed into temporal folds by entry date.

    `on_fold(i, f_start, f_end, fold_result)` is an optional progress callback.
    """
    symbols = list(symbols)
    folds = split_folds(start, end, n_folds)

    # Single full run — preserves each strategy's lookback/warmup.
    report = run_backtest_for_universe(
        conn, symbols, cfg=cfg,
        start_date=start.isoformat(), end_date=end.isoformat(), progress_every=0,
    )

    fold_sharpes: list[float] = []
    fold_detail: list[dict] = []
    for i, (f_start, f_end) in enumerate(folds, start=1):
        in_fold = [t for t in report.trades
                   if f_start <= _entry_date(t) <= f_end]
        if in_fold:
            sharpe = metrics.summarize(in_fold, capital=cfg.notional_per_trade)["net_sharpe"]
        else:
            sharpe = 0.0
        fold_sharpes.append(sharpe)
        detail = {
            "fold": i, "start": f_start.isoformat(), "end": f_end.isoformat(),
            "trades": len(in_fold), "net_sharpe": sharpe,
        }
        fold_detail.append(detail)
        if on_fold:
            on_fold(i, f_start, f_end, detail)

    # Average over folds that actually have trades (empty folds aren't evidence
    # either way — averaging in their 0.0 would dilute the signal).
    scored = [d["net_sharpe"] for d in fold_detail if d["trades"] > 0]
    avg = round(sum(scored) / len(scored), 3) if scored else 0.0
    n_pos = sum(1 for s in scored if s > 0)
    return {
        "n_folds": len(folds),
        "folds_with_trades": len(scored),
        "avg_sharpe": avg,
        "folds_positive": n_pos,
        "passes": len(scored) > 0 and avg > 0,   # task 11.2 pass condition
        "fold_detail": fold_detail,
    }


def _entry_date(trade) -> date:
    return datetime.fromtimestamp(trade.entry_ts, tz=_IST).date()
