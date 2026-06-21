"""Read access to weekly_ranking — the weekly positional ranking snapshots."""
from __future__ import annotations

import sqlite3

from ..introspect import table_exists


class RankingsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def tables_ready(self) -> bool:
        return table_exists(self.conn, "weekly_ranking")

    def list_weeks(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT week_date, COUNT(*) AS candidates, "
            "SUM(CASE WHEN selected=1 THEN 1 ELSE 0 END) AS selected, "
            "SUM(CASE WHEN excluded=1 THEN 1 ELSE 0 END) AS excluded "
            "FROM weekly_ranking GROUP BY week_date ORDER BY week_date DESC").fetchall()

    def week_rows(self, week_date: str, *, selected_only: bool = False) -> list[sqlite3.Row]:
        q = "SELECT * FROM weekly_ranking WHERE week_date=? "
        if selected_only:
            q += "AND selected=1 "
        q += "ORDER BY selected DESC, adj_score DESC"
        return self.conn.execute(q, (week_date,)).fetchall()

    def latest_week(self) -> str | None:
        r = self.conn.execute("SELECT MAX(week_date) FROM weekly_ranking").fetchone()
        return r[0] if r else None
