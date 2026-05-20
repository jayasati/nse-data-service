"""
Stocks hitting upper or lower circuit.

NSE endpoint: /api/live-analysis-price-band-hitter?index={upper|lower}

Returns three category buckets in one response:
  AllSec  — union of everything
  SecGtr20 — stocks > ₹20
  SecLwr20 — stocks ≤ ₹20

We flatten all three into one table tagged with category. AllSec is
redundant with SecGtr20 ∪ SecLwr20 in principle but NSE serves them all
and ranks can differ slightly, so we store everything.

upper_circuit / lower_circuit are signal types in §8.2.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"

CATEGORIES = ("AllSec", "SecGtr20", "SecLwr20")


class _BandHitsBase(SnapshotCollector):
    table = "raw_band_hits"
    pk_cols = ("symbol", "band", "category", "as_of")

    # Subclass sets:
    band: str = ""         # 'upper' | 'lower'
    nse_param: str = ""    # 'upper' | 'lower'

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/live-analysis-price-band-hitter",
            params={"index": self.nse_param},
            referer=f"{NSE_BASE}/market-data/price-band-hitter",
            response_type="json",
            meta={"band": self.band},
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        as_of = int(time.time())
        band = (request.meta or {}).get("band", self.band)

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
                    "symbol":     symbol,
                    "as_of":      as_of,
                    "band":       band,
                    "category":   category,
                    "series":     item.get("series"),
                    "ltp":        _f(item.get("ltp") or item.get("lastPrice")),
                    "band_pct":   _f(item.get("band") or item.get("pBand")),
                    "open":       _f(item.get("open") or item.get("openPrice")),
                    "high":       _f(item.get("high") or item.get("dayHigh")),
                    "low":        _f(item.get("low") or item.get("dayLow")),
                    "prev_close": _f(item.get("prevClose") or item.get("previousClose")),
                })
        return rows


class UpperBandHits(_BandHitsBase):
    name = "price_band_upper"
    band = "upper"
    nse_param = "upper"


class LowerBandHits(_BandHitsBase):
    name = "price_band_lower"
    band = "lower"
    nse_param = "lower"


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None