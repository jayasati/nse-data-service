"""
Experiment registry writer (FEATURE_CHECKLIST Phase 3, Week 11, task 11.1).

Records one `backtest_registry` row per evaluated strategy run — the durable
promote/shelve ledger. Pure-ish: `param_hash` and `decide_verdict` are unit
tested; `record_run` writes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict

from .types import StrategyConfig

# Gate thresholds (task 11.3).
PROMOTE_NET_SHARPE = 0.5


def param_hash(cfg: StrategyConfig) -> str:
    """Stable sha256 over the strategy's params, so a (re-)run is identifiable."""
    payload = json.dumps(asdict(cfg), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def decide_verdict(net_sharpe: float, cpcv_avg: float | None) -> str:
    """Task 11.3 decision rule.

        net Sharpe > 0.5 AND CPCV avg positive   -> promoted
        net Sharpe < 0   OR  CPCV avg negative    -> shelved
        otherwise                                 -> needs_work
    """
    if net_sharpe > PROMOTE_NET_SHARPE and cpcv_avg is not None and cpcv_avg > 0:
        return "promoted"
    if net_sharpe < 0 or (cpcv_avg is not None and cpcv_avg < 0):
        return "shelved"
    return "needs_work"


def record_run(
    conn: sqlite3.Connection,
    *,
    run_date: str,
    strategy_name: str,
    cfg: StrategyConfig,
    date_range: str,
    metrics: dict,
    cpcv_avg_sharpe: float | None = None,
    cpcv_folds_pos: int | None = None,
    verdict: str | None = None,
    notes: str | None = None,
) -> int:
    """Insert a registry row. `metrics` is a backtester.metrics.summarize() dict.

    `verdict` defaults to `decide_verdict(net_sharpe, cpcv_avg_sharpe)`.
    """
    if verdict is None:
        verdict = decide_verdict(metrics.get("net_sharpe", 0.0), cpcv_avg_sharpe)

    cur = conn.execute(
        """
        INSERT INTO backtest_registry (
            run_date, strategy_name, param_hash, date_range,
            net_sharpe, gross_sharpe, win_rate, profit_factor,
            max_drawdown_pct, n_trades, cost_drag_pct,
            cpcv_avg_sharpe, cpcv_folds_pos, verdict, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_date, strategy_name, param_hash(cfg), date_range,
            metrics.get("net_sharpe"), metrics.get("gross_sharpe"),
            metrics.get("win_rate"), metrics.get("profit_factor"),
            metrics.get("max_drawdown_pct"), metrics.get("n_trades"),
            metrics.get("cost_drag_sharpe"),
            cpcv_avg_sharpe, cpcv_folds_pos, verdict, notes,
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)
