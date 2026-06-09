"""Read access to the earnings-reaction engine (signals + outcomes + setups).

Services depend on this interface; the route layer never sees SQL. The odds math
itself lives in signals/earnings_odds.py (one source of truth, shared with the
Telegram alert) — this repository only fetches the rows the dashboard lists.
"""
from __future__ import annotations

import sqlite3

from ..introspect import _table_columns, table_exists


class EarningsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def tables_ready(self) -> bool:
        # Needs signals + signal_outcomes AND the E3 `direction` column (migration
        # 057). If migrations aren't applied yet, report not-ready so the route
        # returns 503 gracefully rather than erroring on a missing column.
        if not (table_exists(self.conn, "signals") and table_exists(self.conn, "signal_outcomes")):
            return False
        return "direction" in _table_columns(self.conn, "signals")

    def coverage(self) -> sqlite3.Row:
        """Count of earnings_direction signals and how many are settled (ret_1d)."""
        return self.conn.execute(
            """SELECT
                 COUNT(*)                                                   AS total,
                 SUM(CASE WHEN so.ret_1d IS NOT NULL THEN 1 ELSE 0 END)     AS settled,
                 SUM(COALESCE(s.direction,'long') = 'long')                 AS longs,
                 SUM(COALESCE(s.direction,'long') = 'short')                AS shorts
               FROM signals s
               LEFT JOIN signal_outcomes so ON so.signal_id = s.id
               WHERE s.signal_type = 'earnings_direction'"""
        ).fetchone()

    def recent_reactions(
        self, *, direction: str | None = None, limit: int = 100,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT s.symbol, s.detected_at, COALESCE(s.direction,'long') AS direction, "
            "s.price, s.price_change_pct, s.confidence, "
            "so.ret_1d, so.ret_3d, so.hit_t1 "
            "FROM signals s LEFT JOIN signal_outcomes so ON so.signal_id = s.id "
            "WHERE s.signal_type = 'earnings_direction'"
        )
        params: list = []
        if direction:
            sql += " AND COALESCE(s.direction,'long') = ?"
            params.append(direction)
        sql += " ORDER BY s.detected_at DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def upcoming(self, *, limit: int = 100) -> list[sqlite3.Row]:
        """Pre-event setups (what's priced in), most recent event first."""
        if not table_exists(self.conn, "earnings_setups"):
            return []
        return self.conn.execute(
            "SELECT symbol, event_date, run_up_5d, run_up_class, implied_move_pct, "
            "pcr, fundamental_class, expectation_proxy_score, consensus_rev_est, flagged_at "
            "FROM earnings_setups ORDER BY event_date DESC, symbol ASC LIMIT ?",
            (limit,),
        ).fetchall()
