"""
Live equity quotes — fetch LTP/volume for an index's constituents.

NSE endpoint: /api/equity-stock-indices?index=<NAME>
(NSE renamed this from the camelCase /api/equity-stockIndices around
2026-05-22; the old path now 404s. Response shape is unchanged.)

Returns ~50 stocks with one row per symbol containing LTP, OHLC of the day,
volume, value, 52-week high/low. The first row of the response is the index
itself (its `symbol` field equals the index name); we drop it because index
quotes live in raw_indices, not raw_equity_quotes.

This is a parameterized collector. The base class (LiveEquity) defaults to
NIFTY 50. To poll a different index, subclass and override index_name + name.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"


class LiveEquity(SnapshotCollector):
    name = "live_equity_nifty50"
    table = "raw_equity_quotes"
    pk_cols = ("symbol", "index_name", "as_of")

    index_name: str = "NIFTY 50"   # override in subclass for other indices

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/equity-stock-indices",
            params={"index": self.index_name},
            referer=f"{NSE_BASE}/market-data/live-equity-market",
            response_type="json",
            meta={"index_name": self.index_name},
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        items = data.get("data") or []
        if not isinstance(items, list):
            return []

        as_of = int(time.time())
        index_name = (request.meta or {}).get("index_name", self.index_name)
        # The index appears as the first row with symbol == index_name.
        # Compare case-insensitively to be defensive about NSE variations.
        index_marker = index_name.strip().upper()

        rows: list[Row] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            if not symbol:
                continue
            # Drop the index-itself row — those quotes live in raw_indices.
            if symbol.upper() == index_marker:
                continue

            rows.append({
                "symbol":      symbol,
                "as_of":       as_of,
                "index_name":  index_name,
                "last_price":  _f(item.get("lastPrice")),
                "open":        _f(item.get("open")),
                "day_high":    _f(item.get("dayHigh")),
                "day_low":     _f(item.get("dayLow")),
                "prev_close":  _f(item.get("previousClose")),
                "change":      _f(item.get("change")),
                "pct_change":  _f(item.get("pChange")),
                "volume":      _i(item.get("totalTradedVolume")),
                "value":       _f(item.get("totalTradedValue")),
                "year_high":   _f(item.get("yearHigh")),
                "year_low":    _f(item.get("yearLow")),
            })
        return rows


class LiveEquityTotalMarket(LiveEquity):
    """Live quotes for the NIFTY TOTAL MARKET index — ~750 stocks, i.e. the
    full liquid tradeable universe, in a single call. Polled at 1-min cadence
    during market hours to power the dashboard's live broad-market view.

    Shares raw_equity_quotes with LiveEquity; the index_name column keeps the
    two feeds distinct (NIFTY 50 vs NIFTY TOTAL MARKET)."""

    name = "live_equity_total_market"
    index_name = "NIFTY TOTAL MARKET"


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None