"""Read access to backtest_runs and backtest_trades.

Services depend on this interface; the route layer never sees SQL.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..introspect import table_exists


class BacktestRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def tables_ready(self) -> bool:
        return (table_exists(self.conn, "backtest_runs")
                and table_exists(self.conn, "backtest_trades"))

    # ---- runs ----
    def list_runs(self, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT id, strategy, universe, symbols_count, start_date, end_date,
                      leverage, total_signals, total_trades, wins, losses,
                      pnl_raw, pnl_leveraged, max_dd_raw, created_at, notes
               FROM backtest_runs
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """SELECT id, strategy, params_json, universe, symbols_count,
                      start_date, end_date, leverage,
                      total_signals, total_trades, wins, losses,
                      pnl_raw, pnl_leveraged, max_dd_raw, created_at, notes
               FROM backtest_runs WHERE id = ?""",
            (run_id,),
        ).fetchone()

    # ---- trades ----
    def trades(
        self, run_id: int, *,
        symbol: str | None = None,
        limit: int = 100, offset: int = 0,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT symbol, direction, setup_ts, entry_ts, entry_price, sl, target, "
            "exit_ts, exit_price, exit_reason, qty, pnl_raw, pnl_leveraged, "
            "rr_at_entry "
            "FROM backtest_trades WHERE run_id = ?"
        )
        params: list[Any] = [run_id]
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol.upper())
        sql += " ORDER BY entry_ts ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self.conn.execute(sql, params).fetchall()

    def trades_for_equity_curve(self, run_id: int) -> list[sqlite3.Row]:
        """All trades ordered by entry_ts — used to plot cumulative P&L."""
        return self.conn.execute(
            "SELECT entry_ts, pnl_raw, pnl_leveraged FROM backtest_trades "
            "WHERE run_id = ? ORDER BY entry_ts ASC",
            (run_id,),
        ).fetchall()

    def by_symbol(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT symbol,
                      COUNT(*)                                  AS trades,
                      SUM(CASE WHEN pnl_raw > 0 THEN 1 ELSE 0 END) AS wins,
                      SUM(CASE WHEN pnl_raw < 0 THEN 1 ELSE 0 END) AS losses,
                      SUM(pnl_raw)                              AS pnl_raw,
                      SUM(pnl_leveraged)                        AS pnl_leveraged
               FROM backtest_trades WHERE run_id = ?
               GROUP BY symbol
               ORDER BY pnl_leveraged DESC""",
            (run_id,),
        ).fetchall()
