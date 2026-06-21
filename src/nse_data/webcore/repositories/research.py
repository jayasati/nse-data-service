"""Read access for the stock-research page — candles, delivery, news, deals, holdings."""
from __future__ import annotations

import sqlite3

from ..introspect import table_exists


class ResearchRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def tables_ready(self) -> bool:
        return table_exists(self.conn, "raw_intraday_candles")

    def daily_bars(self, sym: str, start_ep: int, end_ep: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT ts, open, high, low, close, volume FROM raw_intraday_candles "
            "WHERE symbol=? AND interval='day' AND ts>=? AND ts<=? ORDER BY ts",
            (sym, start_ep, end_ep)).fetchall()

    def minute_closes(self, sym: str, day_start_ep: int, day_end_ep: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT ts, close FROM raw_intraday_candles WHERE symbol=? AND interval='minute' "
            "AND ts>=? AND ts<? ORDER BY ts", (sym, day_start_ep, day_end_ep)).fetchall()

    def delivery(self, sym: str, start: str, end: str) -> dict:
        if not table_exists(self.conn, "raw_bhavcopy_cm"):
            return {}
        return {d: dp for d, dp in self.conn.execute(
            "SELECT date, delivery_pct FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' "
            "AND date>=? AND date<=?", (sym, start, end)).fetchall()}

    def news(self, sym: str, start_ep: int, end_ep: int) -> list[sqlite3.Row]:
        if not table_exists(self.conn, "raw_news"):
            return []
        return self.conn.execute(
            "SELECT published_epoch, headline, source, url FROM raw_news WHERE symbol=? "
            "AND published_epoch>=? AND published_epoch<=? ORDER BY published_epoch",
            (sym, start_ep, end_ep)).fetchall()

    def deals(self, sym: str) -> list[sqlite3.Row]:
        if not table_exists(self.conn, "raw_large_deals"):
            return []
        return self.conn.execute(
            "SELECT deal_date, deal_type, client_name, buy_sell, quantity, weighted_avg_price "
            "FROM raw_large_deals WHERE symbol=? AND deal_type IN ('block','bulk') "
            "ORDER BY deal_date DESC LIMIT 30", (sym,)).fetchall()

    def holdings(self, sym: str) -> list[sqlite3.Row]:
        if not table_exists(self.conn, "raw_shareholding_quarterly"):
            return []
        return self.conn.execute(
            "SELECT qe_date, promoter_pct, fii_pct, dii_pct, promoter_pledge_pct "
            "FROM raw_shareholding_quarterly WHERE symbol=? ORDER BY qe_date DESC LIMIT 8",
            (sym,)).fetchall()
