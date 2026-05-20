"""
Top gainers / losers from /api/live-analysis-variations.

NSE returns 7 categories per call:
  NIFTY, BANKNIFTY, NIFTYNEXT50, SecGtr20, SecLwr20, FOSec, allSec

Each category has its own ranked list. We flatten all categories into one
table, tagging each row with both `direction` (gainer/loser) and `category`.

Two instances run — one for gainers (?index=gainers), one for losers
(?index=loosers — NSE's spelling). Different URLs, same shape, same parser.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"

# NSE's categories. The "loosers" misspelling is in NSE's URL parameter, not
# our code. The categories themselves are the keys at the top level of the
# response payload. Listed here so we know which subkeys to walk.
CATEGORIES = ("NIFTY", "BANKNIFTY", "NIFTYNEXT50", "SecGtr20", "SecLwr20", "FOSec", "allSec")


class _MoversBase(SnapshotCollector):
    table = "raw_market_movers"
    pk_cols = ("symbol", "direction", "category", "as_of")

    # Subclass sets these:
    direction: str = ""     # 'gainer' | 'loser'
    nse_param: str = ""     # 'gainers' | 'loosers'

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/live-analysis-variations",
            params={"index": self.nse_param},
            referer=f"{NSE_BASE}/market-data/top-gainers-losers",
            response_type="json",
            meta={"direction": self.direction},
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        as_of = int(time.time())
        direction = (request.meta or {}).get("direction", self.direction)
        rows: list[Row] = []
        for category in CATEGORIES:
            block = data.get(category)
            if not isinstance(block, dict):
                continue
            items = block.get("data") or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                symbol = (item.get("symbol") or "").strip()
                if not symbol:
                    continue
                rows.append({
                    "symbol":      symbol,
                    "as_of":       as_of,
                    "direction":   direction,
                    "category":    category,
                    "last_price":  _f(item.get("ltp") or item.get("lastPrice")),
                    "open":        _f(item.get("open_price") or item.get("openPrice")),
                    "day_high":    _f(item.get("high_price") or item.get("dayHigh")),
                    "day_low":     _f(item.get("low_price") or item.get("dayLow")),
                    "prev_close":  _f(item.get("previousPrice") or item.get("previousClose")),
                    "pct_change":  _f(item.get("perChange") or item.get("pChange")),
                    "volume":      _i(item.get("trade_quantity") or item.get("totalTradedVolume")),
                    "value":       _f(item.get("turnover") or item.get("totalTradedValue")),
                })
        return rows


class Gainers(_MoversBase):
    name = "gainers"
    direction = "gainer"
    nse_param = "gainers"


class Losers(_MoversBase):
    name = "losers"
    direction = "loser"
    nse_param = "loosers"   # NSE's spelling


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