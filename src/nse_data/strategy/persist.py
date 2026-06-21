"""Generic backtest persistence → strategy_runs / strategy_trades (migration 096).

Strategy-agnostic: any strategy maps its trades to the common dict shape and calls `persist_run`.
The UI (webcore strategy_analytics) reads ONLY these tables, so a new strategy shows up in the
dashboard — metrics, drill-downs, trades table — with zero UI changes.
"""
from __future__ import annotations

import json
import sqlite3
import time

from .daily_sweep.walk_forward import REGIMES

_TRADE_COLS = ("symbol", "direction", "daily_trend", "sweep_ts", "bos_ts", "fvg_low", "fvg_high",
               "entry_ts", "exit_ts", "entry_price", "exit_price", "stop", "target", "qty",
               "rr", "rr_achieved", "net_pnl", "net_pct", "exit_reason", "regime")


def regime_for(date_iso: str | None) -> str:
    """Which named market regime a trade's date falls in (covid/bull/sideways/current)."""
    if not date_iso:
        return "other"
    for name, (s, e) in REGIMES.items():
        if s <= date_iso <= e:
            return name
    return "other"


def persist_run(conn: sqlite3.Connection, *, strategy: str, params: dict, metrics: dict,
                trades: list[dict], start_date: str | None = None, end_date: str | None = None,
                notes: str | None = None) -> int:
    """Insert one run + its trades. `trades` are dicts keyed by `_TRADE_COLS`. Returns run_id."""
    cur = conn.execute(
        "INSERT INTO strategy_runs (strategy, run_date, params_json, n_symbols, n_trades, "
        "win_rate, profit_factor, expectancy, net_pct, max_dd_pct, sharpe, avg_rr, start_date, "
        "end_date, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (strategy, time.strftime("%Y-%m-%d"), json.dumps(params),
         metrics.get("n_symbols"), metrics.get("n"), metrics.get("win_rate"),
         metrics.get("profit_factor"), metrics.get("expectancy"), metrics.get("total_net_pct"),
         metrics.get("max_drawdown_pct"), metrics.get("sharpe"), metrics.get("avg_rr"),
         start_date, end_date, notes, int(time.time())))
    run_id = int(cur.lastrowid)
    conn.executemany(
        f"INSERT INTO strategy_trades (run_id, strategy, {', '.join(_TRADE_COLS)}) "
        f"VALUES (?, ?, {', '.join('?' * len(_TRADE_COLS))})",
        [(run_id, strategy, *[t.get(c) for c in _TRADE_COLS]) for t in trades])
    conn.commit()
    return run_id
