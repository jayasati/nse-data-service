"""Write backtest results to SQLite.

One run + N trades in a single transaction. The run row is inserted first
(to get the auto-increment id), then trades, then the aggregate counters on
the run row are updated.

Read paths live in `webcore/repositories/backtests.py` — keep this module
write-only so the dependency arrow is clean.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from typing import Iterable

from typing import TYPE_CHECKING

from .config import BacktestConfig

if TYPE_CHECKING:
    from .runner import SymbolTrade


def write_run(
    conn: sqlite3.Connection,
    *,
    cfg: BacktestConfig,
    universe: str,
    symbols_count: int,
    start_date: str,
    end_date: str,
    trades: Iterable["SymbolTrade"],
    total_signals: int,
    notes: str | None = None,
) -> int:
    """Insert one row into backtest_runs + N rows into backtest_trades.

    All-or-nothing transaction. Returns the new run_id.
    """
    trades = list(trades)
    wins, losses, pnl_raw_sum = _summarize(trades)
    pnl_lev_sum = pnl_raw_sum * cfg.leverage
    max_dd = _max_drawdown_raw(trades)

    params_json = json.dumps(_params_dict(cfg), sort_keys=True)

    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        cur.execute(
            """
            INSERT INTO backtest_runs (
                strategy, params_json, universe, symbols_count,
                start_date, end_date, leverage,
                total_signals, total_trades, wins, losses,
                pnl_raw, pnl_leveraged, max_dd_raw,
                created_at, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cfg.strategy, params_json, universe, symbols_count,
                start_date, end_date, cfg.leverage,
                total_signals, len(trades), wins, losses,
                pnl_raw_sum, pnl_lev_sum, max_dd,
                int(time.time()), notes,
            ),
        )
        run_id = cur.lastrowid
        assert run_id is not None

        if trades:
            cur.executemany(
                """
                INSERT INTO backtest_trades (
                    run_id, symbol, direction, setup_ts, entry_ts, entry_price,
                    sl, target, exit_ts, exit_price, exit_reason, qty,
                    pnl_raw, pnl_leveraged, rr_at_entry
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_trade_row(run_id, t, cfg.leverage) for t in trades],
            )
        conn.commit()
        return int(run_id)
    except Exception:
        conn.rollback()
        raise


# ----------------------------------------------------------- helpers

def _params_dict(cfg: BacktestConfig) -> dict:
    """A round-trippable view of cfg for params_json. Trims `extras` to
    plain JSON-safe primitives."""
    d = asdict(cfg)
    extras = d.get("extras") or {}
    d["extras"] = {k: v for k, v in extras.items()
                   if isinstance(v, (str, int, float, bool, type(None)))}
    return d


def _trade_row(run_id: int, t, leverage: float):  # noqa: ARG001
    """Tuple matching the INSERT in write_run. Expects a SymbolTrade
    (runner.py) — flat fields, no methods."""
    return (
        run_id, t.symbol, t.direction,
        t.setup_ts, t.entry_ts, t.entry_price,
        t.sl, t.target,
        t.exit_ts, t.exit_price, t.exit_reason, t.qty,
        t.pnl_raw, t.pnl_leveraged, t.rr_at_entry,
    )


def _summarize(trades) -> tuple[int, int, float]:
    wins, losses, pnl = 0, 0, 0.0
    for t in trades:
        p = t.pnl_raw
        pnl += p
        if p > 0:
            wins += 1
        elif p < 0:
            losses += 1
    return wins, losses, pnl


def _max_drawdown_raw(trades) -> float:
    """Worst peak-to-trough on cumulative raw P&L (in absolute INR, negative)."""
    cum, peak, worst = 0.0, 0.0, 0.0
    for t in trades:
        cum += t.pnl_raw
        peak = max(peak, cum)
        dd = cum - peak  # <= 0
        worst = min(worst, dd)
    return worst
