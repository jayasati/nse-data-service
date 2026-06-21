"""Read access to strategy_runs / strategy_trades (the generic strategy-analytics tables).

Services depend on this; the route layer never sees SQL. The drill-down dimensions (symbol /
day / week / month / regime / direction / exit_reason) are a whitelist mapped to safe SQL group
expressions — the UI's "study any field" comes from one parametric query.
"""
from __future__ import annotations

import sqlite3

from ..introspect import table_exists

# whitelist: dimension name → SQL group expression over strategy_trades
DIMENSIONS = {
    "symbol": "symbol",
    "day": "substr(entry_ts, 1, 10)",
    "week": "strftime('%Y-W%W', entry_ts)",
    "month": "substr(entry_ts, 1, 7)",
    "regime": "regime",
    "direction": "direction",
    "exit_reason": "exit_reason",
    "daily_trend": "daily_trend",
}


class StrategyAnalyticsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def tables_ready(self) -> bool:
        return (table_exists(self.conn, "strategy_runs")
                and table_exists(self.conn, "strategy_trades"))

    def list_strategies(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT strategy, COUNT(*) AS runs, MAX(id) AS latest_run_id,
                      MAX(created_at) AS last_run
               FROM strategy_runs GROUP BY strategy ORDER BY last_run DESC""").fetchall()

    def list_runs(self, limit: int, strategy: str | None = None) -> list[sqlite3.Row]:
        q = ("SELECT id, strategy, run_date, n_symbols, n_trades, win_rate, profit_factor, "
             "expectancy, net_pct, max_dd_pct, sharpe, avg_rr, start_date, end_date, notes, "
             "created_at FROM strategy_runs ")
        args: list = []
        if strategy:
            q += "WHERE strategy=? "
            args.append(strategy)
        q += "ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(limit)
        return self.conn.execute(q, args).fetchall()

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM strategy_runs WHERE id=?", (run_id,)).fetchone()

    def trades(self, run_id: int, *, symbol: str | None = None, direction: str | None = None,
               exit_reason: str | None = None, limit: int = 200, offset: int = 0
               ) -> list[sqlite3.Row]:
        q = "SELECT * FROM strategy_trades WHERE run_id=? "
        args: list = [run_id]
        for col, val in (("symbol", symbol), ("direction", direction), ("exit_reason", exit_reason)):
            if val:
                q += f"AND {col}=? "
                args.append(val)
        q += "ORDER BY entry_ts LIMIT ? OFFSET ?"
        args += [limit, offset]
        return self.conn.execute(q, args).fetchall()

    def pnl_by(self, run_id: int, dimension: str) -> list[sqlite3.Row]:
        """P&L study grouped by a whitelisted dimension. Raises KeyError for an unknown dim."""
        expr = DIMENSIONS[dimension]
        return self.conn.execute(
            f"SELECT {expr} AS bucket, COUNT(*) AS trades, "
            "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins, "
            "SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) AS losses, "
            "ROUND(SUM(net_pnl), 2) AS pnl, ROUND(SUM(net_pct), 3) AS pct, "
            "ROUND(AVG(rr_achieved), 2) AS avg_rr "
            f"FROM strategy_trades WHERE run_id=? GROUP BY bucket ORDER BY bucket", (run_id,)
        ).fetchall()

    def equity_curve(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT exit_ts, net_pnl FROM strategy_trades WHERE run_id=? "
            "AND exit_ts IS NOT NULL ORDER BY exit_ts", (run_id,)).fetchall()

    # ---- live signals (system-generated, forward-test) ----
    def live_signals(self, limit: int) -> list[sqlite3.Row]:
        if not table_exists(self.conn, "daily_sweep_signals"):
            return []
        return self.conn.execute(
            "SELECT 'daily_sweep' AS strategy, symbol, direction, daily_trend, sweep_time, "
            "bos_time, fvg_low, fvg_high, entry_time, entry_price, stop, target, qty, rr, "
            "alerted_at FROM daily_sweep_signals ORDER BY alerted_at DESC LIMIT ?", (limit,)
        ).fetchall()
