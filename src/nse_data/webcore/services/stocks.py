"""Business logic for the stocks surface.

Orchestrates the repository (data) and transforms (candle math) into the JSON
shapes the API/frontend expect. Framework-agnostic: raises domain exceptions
(webcore.errors) that the route layer maps to HTTP status codes.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from ...scheduler.market_hours import IST
from .. import transforms
from ..config import BHAV, IST_OFFSET, LIVE_FRESH_SECS, RANK_EOD, RANK_LIVE
from ..errors import BadRequest, NotFound, Unavailable
from ..repositories.stocks import StockRepository

_INTERVALS = ("1m", "5m", "1d", "1w")


class StockService:
    def __init__(self, repo: StockRepository):
        self.repo = repo

    # ------------------------------------------------------------------ top
    def top(self, by: str, source: str, limit: int) -> dict:
        if by not in RANK_EOD:
            raise BadRequest(f"by must be one of {sorted(RANK_EOD)}")
        if source not in ("auto", "live", "eod"):
            raise BadRequest("source must be auto|live|eod")

        as_of = self.repo.live_as_of()
        fresh = as_of is not None and (time.time() - as_of) <= LIVE_FRESH_SECS
        if source == "live" or (source == "auto" and fresh):
            if as_of is None:
                raise Unavailable("no live snapshot available")
            stocks = [{
                "rank": i, "symbol": r["symbol"], "last": r["last_price"],
                "prev_close": r["prev_close"], "change": r["change"],
                "pct_change": round(r["pct_change"], 2) if r["pct_change"] is not None else None,
                "volume": r["volume"],
                "turnover_cr": round(r["value"] / 1e7, 2) if r["value"] is not None else None,
                "delivery_pct": None,
            } for i, r in enumerate(self.repo.top_live(RANK_LIVE[by], as_of, limit), 1)]
            return {"source": "live", "as_of": as_of, "date": None,
                    "by": by, "count": len(stocks), "stocks": stocks}

        # EOD fallback
        if not self.repo.has_table(BHAV):
            raise Unavailable(f"{BHAV} not available")
        date = self.repo.latest_bhav_date()
        if not date:
            return {"source": "eod", "date": None, "by": by, "count": 0, "stocks": []}
        stocks = []
        for i, r in enumerate(self.repo.top_eod(date, RANK_EOD[by], limit), 1):
            close, prev = r["close"], r["prev_close"]
            pct = (100.0 * (close - prev) / prev) if prev else None
            stocks.append({
                "rank": i, "symbol": r["symbol"], "last": close, "prev_close": prev,
                "change": (close - prev) if (close is not None and prev is not None) else None,
                "pct_change": round(pct, 2) if pct is not None else None,
                "volume": r["volume"],
                "turnover_cr": round(r["turnover_lacs"] / 100.0, 2) if r["turnover_lacs"] is not None else None,
                "delivery_pct": r["delivery_pct"],
            })
        return {"source": "eod", "date": date, "as_of": None,
                "by": by, "count": len(stocks), "stocks": stocks}

    # -------------------------------------------------------------- history
    def history(self, symbol: str, interval: str, days: int) -> dict:
        if interval not in _INTERVALS:
            raise BadRequest("interval must be 1m|5m|1d|1w")
        symbol = symbol.upper()

        if interval in ("1m", "5m"):
            # One MINUTE backfill powers both: full 1-min series (history + today),
            # then resample for 5m.
            hist = self._backfill_candles(symbol, "minute")
            live = transforms.line_to_candles(
                transforms.intraday_line(self.repo.intraday_snapshots(symbol)))
            base = transforms.merge_candles(hist, live)
            cutoff = (time.time() + IST_OFFSET) - days * 86400
            base = [c for c in base if c["time"] >= cutoff]
            points = transforms.resample_intraday(base, 300) if interval == "5m" else base
        else:
            if not self.repo.has_table(BHAV):
                raise Unavailable(f"{BHAV} not available")
            daily = self._daily_candles(symbol, days, stitch_live=True)
            points = transforms.resample_weekly(daily) if interval == "1w" else daily

        return {"symbol": symbol, "interval": interval, "type": "candles",
                "count": len(points), "points": points}

    def _backfill_candles(self, symbol: str, interval: str) -> list[dict]:
        return [{"time": r["ts"] + IST_OFFSET, "open": r["open"], "high": r["high"],
                 "low": r["low"], "close": r["close"], "volume": r["volume"]}
                for r in self.repo.backfill_candles(symbol, interval)]

    def _daily_candles(self, symbol: str, days: int, stitch_live: bool) -> list[dict]:
        candles = [{"time": r["date"], "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"], "volume": r["volume"]}
                   for r in reversed(self.repo.daily_rows(symbol, days))]
        if stitch_live:
            as_of = self.repo.live_as_of()
            if as_of is not None:
                lv = self.repo.live_snapshot(symbol, as_of)
                if lv and lv["last_price"] is not None:
                    today = datetime.fromtimestamp(as_of, tz=timezone.utc).astimezone(IST).date().isoformat()
                    lc = {"time": today,
                          "open": lv["open"] if lv["open"] is not None else lv["last_price"],
                          "high": lv["day_high"] if lv["day_high"] is not None else lv["last_price"],
                          "low": lv["day_low"] if lv["day_low"] is not None else lv["last_price"],
                          "close": lv["last_price"], "volume": lv["volume"]}
                    if candles and candles[-1]["time"] == today:
                        candles[-1] = lc
                    else:
                        candles.append(lc)
        return candles

    # ---------------------------------------------------------- indicators
    def indicators(self, symbol: str, limit: int, *, cadence: str | None = None) -> dict:
        """
        Computed-indicator series for `symbol`, registry-driven.

        Every Indicator registered in nse_data.indicators.registry contributes
        one block of the form
            {name: {table, columns, pane, cadence, points: [{<time>, <cols...>}]}}
        Dashboard filters by `block.cadence` to match the chart's timeframe
        and reads `block.pane` to decide overlay vs sub-pane rendering.

        For intraday indicators the `ts` is shifted by IST_OFFSET on the way
        out so lightweight-charts (a UTC time scale) displays IST wall-clock
        — same convention `_backfill_candles` uses for the price bars.

        `limit` caps rows per indicator (most-recent first then reversed).
        `cadence`, if set, filters the registry to one cadence — useful when
        the dashboard knows it's on intraday vs daily already.
        """
        from ...indicators.registry import INDICATORS  # local import: optional dep

        symbol = symbol.upper()
        out: dict[str, dict] = {}
        for ind in INDICATORS:
            if cadence is not None and ind.cadence != cadence:
                continue
            time_col = ind.pk_cols[1]
            rows = self.repo.indicator_rows(
                ind.table, symbol, time_col, ind.output_columns, limit,
            )
            offset = IST_OFFSET if time_col == "ts" else 0
            points = [
                {time_col: (r[time_col] + offset) if offset else r[time_col],
                 **{c: r[c] for c in ind.output_columns}}
                for r in rows
            ]
            out[ind.name] = {
                "table": ind.table,
                "columns": list(ind.output_columns),
                "pane": ind.pane,
                "cadence": ind.cadence,
                "time_key": time_col,         # frontend uses this to read points
                "count": len(points),
                "points": points,
            }
        return {"symbol": symbol, "indicators": out}

    # ----------------------------------------------------------------- meta
    def meta(self, symbol: str) -> dict:
        symbol = symbol.upper()
        as_of = self.repo.live_as_of()
        live = self.repo.live_snapshot(symbol, as_of) if as_of is not None else None
        eod = self.repo.bhav_eod_latest(symbol)
        qm = self.repo.quote_meta(symbol)
        scr = self.repo.screener(symbol)

        if live and live["last_price"] is not None:
            ltp, prev = live["last_price"], live["prev_close"]
            o, hi, lo, vol = live["open"], live["day_high"], live["day_low"], live["volume"]
            is_live = True
        elif eod:
            ltp, prev = eod["close"], eod["prev_close"]
            o, hi, lo, vol = eod["open"], eod["high"], eod["low"], eod["volume"]
            is_live = False
        else:
            raise NotFound(f"no data for {symbol}")

        change = (ltp - prev) if (ltp is not None and prev is not None) else None
        chg_pct = (100.0 * change / prev) if (change is not None and prev) else None
        book = scr["book_value"] if scr else None
        pb = round(ltp / book, 2) if (book and ltp) else None
        mcap = (scr["market_cap"] if scr and scr.get("market_cap") else
                (qm["market_cap_cr"] if qm and qm.get("market_cap_cr") else None))

        return {
            "symbol": symbol, "exchange": "NSE",
            "name": (qm["company_name"] if qm else None) or symbol,
            "is_live": is_live,
            "ltp": ltp, "prev_close": prev, "change": change,
            "change_pct": round(chg_pct, 2) if chg_pct is not None else None,
            "open": o, "high": hi, "low": lo, "volume": vol,
            "week52_high": live["year_high"] if live else None,
            "week52_low": live["year_low"] if live else None,
            "delivery_pct": eod["delivery_pct"] if eod else None,
            "sector": (qm["sector"] if qm else None),
            "industry": (qm["industry"] if qm else None),
            "pe": (scr["stock_pe"] if scr and scr.get("stock_pe") else (qm["pe_ratio"] if qm else None)),
            "pb": pb,
            "div_yield": (scr["dividend_yield"] if scr else None),
            "market_cap_cr": mcap,
            "roce": (scr["roce"] if scr else None),
            "roe": (scr["roe"] if scr else None),
            "is_fno": (bool(qm["is_fno"]) if qm and qm.get("is_fno") is not None else None),
            "beta": None,  # not collected
        }

    # --------------------------------------------------------------- search
    def search(self, q: str, limit: int) -> dict:
        q = q.strip().upper()
        if not self.repo.has_table(BHAV):
            return {"q": q, "results": []}
        date = self.repo.latest_bhav_date()
        if not date:
            return {"q": q, "results": []}
        out = []
        for r in self.repo.search(date, q, limit):
            close, prev = r["close"], r["prev_close"]
            pct = (100.0 * (close - prev) / prev) if prev else None
            out.append({"symbol": r["symbol"], "last": close,
                        "pct_change": round(pct, 2) if pct is not None else None})
        return {"q": q, "results": out}
