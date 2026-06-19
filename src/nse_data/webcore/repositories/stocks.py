"""Stocks data access — every SQL read against the raw price/quote tables.

Repository pattern: services depend on this interface, not on SQL. All reads go
through a single read-only connection injected at construction. Ranking
expressions are passed pre-validated by the service (mapped from RANK_* in
config), so the only interpolation here is over trusted values.
"""

from __future__ import annotations

import sqlite3

from ..config import BHAV, INTRADAY, LIVE_INDEX, QUOTES, QUOTE_META, SCREENER
from ..introspect import detect_ts_column, table_exists, _safe_ident


class StockRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- generic helpers ----
    def has_table(self, name: str) -> bool:
        return table_exists(self.conn, name)

    def _one(self, sql: str, args: tuple):
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    # ---- freshness anchors ----
    def latest_bhav_date(self) -> str | None:
        if not self.has_table(BHAV):
            return None
        row = self.conn.execute(f"SELECT MAX(date) FROM {BHAV}").fetchone()
        return row[0] if row else None

    def live_as_of(self) -> int | None:
        """Newest as_of for the broad live feed, or None."""
        if not self.has_table(QUOTES):
            return None
        row = self.conn.execute(
            f"SELECT MAX(as_of) FROM {QUOTES} WHERE index_name = ?", (LIVE_INDEX,)
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    # ---- top-N lists ----
    def top_live(self, by_expr: str, as_of: int, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            f"""SELECT symbol, last_price, prev_close, change, pct_change, volume, value
                FROM {QUOTES} WHERE index_name = ? AND as_of = ?
                ORDER BY {by_expr} DESC LIMIT ?""",
            (LIVE_INDEX, as_of, limit),
        ).fetchall()

    def top_eod(self, date: str, by_expr: str, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            f"""SELECT symbol, close, prev_close, volume, turnover_lacs, delivery_pct
                FROM {BHAV} WHERE date = ? AND series = 'EQ'
                ORDER BY {by_expr} DESC LIMIT ?""",
            (date, limit),
        ).fetchall()

    # ---- candle sources ----
    def daily_rows(self, symbol: str, days: int,
                   end: str | None = None) -> list[sqlite3.Row]:
        """Last `days` EOD rows, newest-first. With `end` ('YYYY-MM-DD') the
        window ends on/at that date instead of the latest available."""
        if end:
            return self.conn.execute(
                f"""SELECT date, open, high, low, close, volume FROM {BHAV}
                    WHERE symbol = ? AND series = 'EQ' AND date <= ?
                    ORDER BY date DESC LIMIT ?""",
                (symbol, end, days),
            ).fetchall()
        return self.conn.execute(
            f"""SELECT date, open, high, low, close, volume FROM {BHAV}
                WHERE symbol = ? AND series = 'EQ' ORDER BY date DESC LIMIT ?""",
            (symbol, days),
        ).fetchall()

    def score_history(self, symbol: str, days: int) -> list[sqlite3.Row]:
        """Last `days` daily factor-snapshot rows for the symbol, oldest-first —
        the ranking-engine score series the chart view overlays. Empty if the
        feature store (migration 082) hasn't been created yet."""
        if not self.has_table("factor_snapshot"):
            return []
        return self.conn.execute(
            """SELECT snapshot_date, composite, quality, valuation, momentum,
                      turnaround, surprise, liquidity, risk, confidence,
                      sector, sector_rank, sector_n, regime
               FROM factor_snapshot WHERE symbol = ?
               ORDER BY snapshot_date DESC LIMIT ?""",
            (symbol, days),
        ).fetchall()[::-1]

    def intraday_snapshots(self, symbol: str) -> list[sqlite3.Row]:
        """(as_of, last_price, volume) from the live 1-min feed, oldest-first."""
        if not self.has_table(QUOTES):
            return []
        return self.conn.execute(
            f"""SELECT as_of, last_price, volume FROM {QUOTES}
                WHERE index_name = ? AND symbol = ? AND last_price IS NOT NULL
                ORDER BY as_of ASC""",
            (LIVE_INDEX, symbol),
        ).fetchall()

    def backfill_candles(self, symbol: str, interval: str) -> list[sqlite3.Row]:
        """Broker-backfilled OHLC candles (raw_intraday_candles), oldest-first."""
        if not self.has_table(INTRADAY):
            return []
        return self.conn.execute(
            f"""SELECT ts, open, high, low, close, volume FROM {INTRADAY}
                WHERE symbol = ? AND interval = ? ORDER BY ts ASC""",
            (symbol, interval),
        ).fetchall()

    def live_snapshot(self, symbol: str, as_of: int):
        """Full latest live row for a symbol (price header + candle stitch)."""
        return self._one(
            f"""SELECT last_price, open, day_high, day_low, prev_close, change,
                       pct_change, volume, value, year_high, year_low
                FROM {QUOTES} WHERE index_name = ? AND symbol = ? AND as_of = ?""",
            (LIVE_INDEX, symbol, as_of),
        )

    # ---- search & fundamentals ----
    def search(self, date: str, q: str, limit: int) -> list[sqlite3.Row]:
        if q:
            return self.conn.execute(
                f"""SELECT symbol, close, prev_close FROM {BHAV}
                    WHERE date = ? AND series = 'EQ' AND symbol LIKE ?
                    ORDER BY (symbol LIKE ?) DESC, turnover_lacs DESC LIMIT ?""",
                (date, f"%{q}%", f"{q}%", limit),
            ).fetchall()
        return self.conn.execute(
            f"""SELECT symbol, close, prev_close FROM {BHAV}
                WHERE date = ? AND series = 'EQ' ORDER BY turnover_lacs DESC LIMIT ?""",
            (date, limit),
        ).fetchall()

    def bhav_eod_latest(self, symbol: str):
        return self._one(
            f"""SELECT date, open, high, low, close, prev_close, volume,
                       turnover_lacs, delivery_pct FROM {BHAV}
                WHERE symbol = ? AND series = 'EQ' ORDER BY date DESC LIMIT 1""",
            (symbol,),
        )

    def quote_meta(self, symbol: str):
        if not self.has_table(QUOTE_META):
            return None
        return self._one(
            f"""SELECT company_name, sector, industry, pe_ratio, market_cap_cr, is_fno
                FROM {QUOTE_META} WHERE symbol = ?""", (symbol,))

    def screener(self, symbol: str):
        if not self.has_table(SCREENER):
            return None
        return self._one(
            f"""SELECT stock_pe, book_value, dividend_yield, market_cap, roce, roe,
                       debt_to_equity FROM {SCREENER}
                WHERE symbol = ? ORDER BY captured_at DESC LIMIT 1""", (symbol,))

    # ---- computed indicators (indicator_* tables, EOD + intraday) ----
    def indicator_rows(
        self,
        table: str,
        symbol: str,
        time_col: str,
        columns: tuple[str, ...],
        limit: int,
        end: int | str | None = None,
    ) -> list[sqlite3.Row]:
        """
        Return up to `limit` rows of indicator output for `symbol`, oldest-first.

        `time_col` is the PK time column — `date` for EOD indicators (TEXT
        date strings) or `ts` for intraday indicators (INTEGER epoch). Caller
        supplies it from the registered Indicator's `pk_cols[1]`.

        `end`, when given, caps rows at `time_col <= end` (a UTC epoch for `ts`
        columns, a 'YYYY-MM-DD' string for `date`) so the series matches a
        point-in-time chart view.

        Table name, time column, and output columns all come from Indicator
        subclasses owned at import time — `_safe_ident` is belt-and-braces.
        """
        if not self.has_table(table):
            return []
        safe_table = _safe_ident(table)
        safe_time = _safe_ident(time_col)
        safe_cols = ",".join(_safe_ident(c) for c in columns)
        where = "WHERE symbol = ?"
        args: list[object] = [symbol]
        if end is not None:
            where += f" AND {safe_time} <= ?"
            args.append(end)
        args.append(limit)
        rows = self.conn.execute(
            f"""SELECT {safe_time}, {safe_cols} FROM {safe_table}
                {where} ORDER BY {safe_time} DESC LIMIT ?""",
            tuple(args),
        ).fetchall()
        return list(reversed(rows))

    # ---- raw table preview (health page drill-down) ----
    def table_recent(self, name: str, limit: int) -> tuple[str, list[dict]]:
        ts_col, _ = detect_ts_column(self.conn, name)
        order = _safe_ident(ts_col) if ts_col else "rowid"
        rows = self.conn.execute(
            f"SELECT * FROM {_safe_ident(name)} ORDER BY {order} DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return (ts_col or "rowid"), [dict(r) for r in rows]
