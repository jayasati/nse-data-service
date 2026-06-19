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

_INTERVALS = ("1m", "5m", "15m", "30m", "1d", "1w")
# Intraday bucket seconds. The IST session opens 09:15 = 33300s into the chart
# day; anchoring buckets to `33300 % bucket` makes bars start on session opens
# (09:15, 09:45, …) like TradingView/Groww, not the :00/:30 wall-clock grid.
_INTRADAY_BUCKET = {"5m": 300, "15m": 900, "30m": 1800}


def _ist_day_end_charttime(end_date: str) -> float:
    """Right edge of an IST day, in *chart time* (UTC epoch + IST_OFFSET, i.e.
    the IST wall-clock expressed as a fake-UTC epoch — the same space the
    intraday candle `time` lives in). `end_date` is 'YYYY-MM-DD' (IST)."""
    d = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return d.timestamp() + 86400  # next IST midnight


def _ist_day_end_epoch(end_date: str) -> int:
    """Right edge of an IST day as a raw UTC epoch (the units of the stored
    `ts` columns), for `WHERE ts <= ?` indicator filtering."""
    return int(_ist_day_end_charttime(end_date) - IST_OFFSET)


def _last_sessions(candles: list[dict], n: int) -> list[dict]:
    """Keep only the bars belonging to the newest `n` distinct IST trading
    days present in `candles` (oldest-first input, order preserved). Counting
    sessions — not calendar days — means weekends/holidays never eat into the
    window: days=1 is always a full trading session."""
    keep: set[int] = set()
    for c in reversed(candles):
        d = transforms.ist_day(c["time"])
        if d not in keep:
            if len(keep) == n:
                break
            keep.add(d)
    return [c for c in candles if transforms.ist_day(c["time"]) in keep]


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
    def history(self, symbol: str, interval: str, days: int,
                end: str | None = None) -> dict:
        if interval not in _INTERVALS:
            raise BadRequest("interval must be 1m|5m|15m|30m|1d|1w")
        symbol = symbol.upper()

        if interval in ("1m", "5m", "15m", "30m"):
            # The MINUTE backfill powers both: full 1-min series (history + today),
            # then resample for 5m. The 12-36 month tail is stored as native
            # 5-minute bars — merged in for 5m views, and used as the 1m
            # fallback when the requested day predates 1-min coverage. With
            # `end` set we view a past date, so the live snapshot stitch is
            # skipped and the series is clamped to that IST day's right edge.
            # For intraday, `days` counts TRADING SESSIONS (newest N distinct
            # IST days with bars), not calendar days — so days=1 is the live
            # session while the market is open, else the last trading day.
            hist = self._backfill_candles(symbol, "minute")
            tail = self._backfill_candles(symbol, "5minute")
            if end:
                anchor = _ist_day_end_charttime(end)
                base = [c for c in hist if c["time"] <= anchor]
                tail = [c for c in tail if c["time"] <= anchor]
            else:
                live = transforms.line_to_candles(
                    transforms.intraday_line(self.repo.intraday_snapshots(symbol)))
                base = transforms.merge_candles(hist, live)
            if interval == "1m":
                series = base or tail   # pre-coverage day: show native 5-min bars
            else:
                # minute-derived bars win on days both cover; tail fills the rest
                series = transforms.merge_candles(
                    transforms.resample_intraday(base, 300), tail)
                bucket = _INTRADAY_BUCKET[interval]
                if bucket > 300:        # 15m/30m: rebucket, session-anchored
                    series = transforms.resample_intraday(
                        series, bucket, phase=33300 % bucket)
            points = _last_sessions(series, days)
        else:
            if not self.repo.has_table(BHAV):
                raise Unavailable(f"{BHAV} not available")
            daily = self._daily_candles(symbol, days, stitch_live=(end is None), end=end)
            points = transforms.resample_weekly(daily) if interval == "1w" else daily

        return {"symbol": symbol, "interval": interval, "type": "candles",
                "count": len(points), "points": points}

    def score_history(self, symbol: str, days: int) -> dict:
        """Daily ranking-engine score series for the chart-view overlay: composite +
        per-engine factor scores + sector rank, oldest-first. `latest` is the most
        recent row (for the score badge); empty points = no snapshots yet."""
        symbol = symbol.upper()
        from ...research import buy_score as bs
        W = bs.REGIME_WEIGHTS["neutral"]
        rows = self.repo.score_history(symbol, days)
        points = []
        for r in rows:
            facs = {k: r[k] for k in ("quality", "valuation", "momentum",
                    "turnaround", "surprise", "liquidity", "risk", "confidence")}
            # lean_score = the OOS-validated Valuation+Surprise composite (the operational
            # signal). buy_score (kitchen-sink) kept for reference — it failed OOS.
            buy, _ = bs.buy_raw(facs, W)
            lean = bs.lean_raw(facs)[0]
            points.append({
                "date": r["snapshot_date"], "lean_score": lean, "buy_score": buy,
                "composite": r["composite"], "sector": r["sector"],
                "sector_rank": r["sector_rank"], "sector_n": r["sector_n"],
                "regime": r["regime"], "factors": facs})
        return {"symbol": symbol, "type": "scores", "count": len(points),
                "points": points, "latest": points[-1] if points else None}

    def buy_card(self, symbol: str) -> dict:
        """Integrated Buy Decision card (grand-prompt v2) from the latest stored
        factor snapshot — regime-adaptive Buy Score, velocity, classification, and a
        Buy/Hold/Reduce/Exit verdict with drivers. `available=False` if no snapshot."""
        from ...research import buy_score as bs
        symbol = symbol.upper()
        row = self.repo.latest_snapshot_row(symbol)
        if not row:
            return {"symbol": symbol, "available": False}
        f = dict(row)
        card = bs.assemble_card(self.repo.conn, symbol, f, f.get("regime"),
                                self.repo.stock_ann_vol(symbol), f["snapshot_date"])
        card["available"] = True
        return card

    def _backfill_candles(self, symbol: str, interval: str) -> list[dict]:
        return [{"time": r["ts"] + IST_OFFSET, "open": r["open"], "high": r["high"],
                 "low": r["low"], "close": r["close"], "volume": r["volume"]}
                for r in self.repo.backfill_candles(symbol, interval)]

    def _broker_daily(self, symbol: str, days: int,
                      end: str | None = None) -> list[dict]:
        """Broker-backfilled EOD candles (interval='day') as daily chart bars
        (date-string time axis), newest `days` rows, optionally clamped to
        `end`. Broker dailies are corporate-action ADJUSTED (continuous across
        bonuses/splits, like TradingView/Groww), unlike raw bhavcopy prints."""
        out = []
        for c in self._backfill_candles(symbol, "day"):
            d = datetime.fromtimestamp(c["time"] - IST_OFFSET, tz=timezone.utc) \
                        .astimezone(IST).date().isoformat()
            if end and d > end:
                break
            out.append({**c, "time": d})
        return out[-days:]

    def _daily_candles(self, symbol: str, days: int, stitch_live: bool,
                       end: str | None = None) -> list[dict]:
        # Prefer the adjusted broker series when it covers the window at least
        # as well as bhavcopy; never mix the two (an adjusted head spliced onto
        # raw prints would fabricate a cliff at every corporate action).
        candles = [{"time": r["date"], "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"], "volume": r["volume"]}
                   for r in reversed(self.repo.daily_rows(symbol, days, end=end))]
        broker = self._broker_daily(symbol, days, end=end)
        if len(broker) >= 0.9 * len(candles):
            candles = broker
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
    def indicators(self, symbol: str, limit: int, *, cadence: str | None = None,
                   end: str | None = None) -> dict:
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
            # Cap rows at the as-of date when one is given: `ts` columns are raw
            # UTC epoch, `date` columns are 'YYYY-MM-DD' strings (compare lexically).
            end_val = None
            if end:
                end_val = _ist_day_end_epoch(end) if time_col == "ts" else end
            rows = self.repo.indicator_rows(
                ind.table, symbol, time_col, ind.output_columns, limit, end=end_val,
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
                # Per-column routing for mixed-scale indicators (ADX/OBV/ratio
                # columns get their own sub-panes instead of the price axis).
                "column_panes": dict(ind.column_panes),
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
