"""
Most-active securities by volume and by value.

NSE endpoints:
  /api/live-analysis-most-active-securities?index=volume   (most-traded shares)
  /api/live-analysis-most-active-securities?index=value    (highest turnover)

Two instances. Different list_type tag. Same shape, same parser.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"


class _MostActiveBase(SnapshotCollector):
    table = "raw_most_active"
    pk_cols = ("symbol", "list_type", "as_of")

    list_type: str = ""   # 'volume' | 'value', set by subclass

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/live-analysis-most-active-securities",
            params={"index": self.list_type},
            referer=f"{NSE_BASE}/market-data/most-active-equities",
            response_type="json",
            meta={"list_type": self.list_type},
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        items = data.get("data") or []
        if not isinstance(items, list):
            return []
        as_of = int(time.time())
        list_type = (request.meta or {}).get("list_type", self.list_type)
        rows: list[Row] = []
        for rank, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            if not symbol:
                continue
            rows.append({
                "symbol":          symbol,
                "as_of":           as_of,
                "list_type":       list_type,
                "rank":            rank,
                "last_price":      _f(item.get("lastPrice")),
                "pct_change":      _f(item.get("pChange")),
                "quantity_traded": _i(item.get("quantityTraded")),
                "total_volume":    _i(item.get("totalTradedVolume")),
                "total_value":     _f(item.get("totalTradedValue")),
                "prev_close":      _f(item.get("previousClose")),
            })
        return rows


class MostActiveByVolume(_MostActiveBase):
    name = "most_active_volume"
    list_type = "volume"


class MostActiveByValue(_MostActiveBase):
    name = "most_active_value"
    list_type = "value"


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