"""Read access to paper_trades (+ the signals they came from).

Services depend on this interface; the route layer never sees SQL. Mirrors
repositories/backtests.py. A "win" is a closed trade with net_pnl > 0; success
rate is computed over decided (closed) trades only.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..introspect import table_exists


class TradesRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def tables_ready(self) -> bool:
        return table_exists(self.conn, "paper_trades")

    def overview(self) -> sqlite3.Row:
        """Portfolio-wide counts + P&L across all paper trades."""
        return self.conn.execute(
            """SELECT
                 COUNT(*)                                                    AS total,
                 SUM(status = 'open')                                        AS open,
                 SUM(status = 'closed')                                      AS closed,
                 SUM(CASE WHEN status='closed' AND net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                 SUM(CASE WHEN status='closed' AND net_pnl < 0 THEN 1 ELSE 0 END) AS losses,
                 SUM(CASE WHEN status='closed' THEN net_pnl  ELSE 0 END)     AS net_pnl,
                 SUM(CASE WHEN status='closed' THEN gross_pnl ELSE 0 END)    AS gross_pnl
               FROM paper_trades"""
        ).fetchone()

    def by_strategy(self) -> list[sqlite3.Row]:
        """Per-signal_type breakdown: counts, win rate inputs, P&L, avg win/loss."""
        return self.conn.execute(
            """SELECT
                 signal_type,
                 COUNT(*)                                                    AS total,
                 SUM(status = 'open')                                        AS open,
                 SUM(status = 'closed')                                      AS closed,
                 SUM(CASE WHEN status='closed' AND net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                 SUM(CASE WHEN status='closed' AND net_pnl < 0 THEN 1 ELSE 0 END) AS losses,
                 SUM(CASE WHEN status='closed' THEN net_pnl ELSE 0 END)      AS net_pnl,
                 AVG(CASE WHEN status='closed' AND net_pnl > 0 THEN net_pnl END) AS avg_win,
                 AVG(CASE WHEN status='closed' AND net_pnl < 0 THEN net_pnl END) AS avg_loss
               FROM paper_trades
               GROUP BY signal_type
               ORDER BY net_pnl DESC"""
        ).fetchall()

    def list_trades(
        self, *,
        status: str | None = None,
        strategy: str | None = None,
        limit: int = 100, offset: int = 0,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT id, signal_id, symbol, signal_type, entry_price, sl_price, "
            "t1_price, entry_time, exit_price, exit_time, exit_reason, "
            "gross_pnl, net_pnl, status FROM paper_trades"
        )
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if strategy:
            clauses.append("signal_type = ?")
            params.append(strategy)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # Newest activity first: exit time if closed, else entry time.
        sql += " ORDER BY COALESCE(exit_time, entry_time) DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self.conn.execute(sql, params).fetchall()
