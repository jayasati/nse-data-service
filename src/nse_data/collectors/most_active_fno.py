"""
Most active F&O contracts by volume and by value.

NSE endpoint: /api/snapshot-derivatives-equity?index=contracts
Returns both 'volume' and 'value' blocks in a single response.

Two collector instances are registered but they share one fetch — the
endpoint serves both list types in one call. We pick the right block in
normalize() based on the instance's list_type.

Each row is a contract: futures (FUTSTK/FUTIDX) or option (OPTSTK/OPTIDX
with strike + option_type). The same underlying may appear in both lists.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"


class _MostActiveFnoBase(SnapshotCollector):
    table = "raw_most_active_fno"
    pk_cols = ("symbol", "list_type", "as_of", "rank")

    # Subclass sets:
    list_type: str = ""   # 'volume' | 'value'

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/snapshot-derivatives-equity",
            params={"index": "contracts"},
            referer=f"{NSE_BASE}/market-data/most-active-equity-contracts",
            response_type="json",
            meta={"list_type": self.list_type},
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        list_type = (request.meta or {}).get("list_type", self.list_type)
        block = data.get(list_type)
        if not isinstance(block, dict):
            return []
        items = block.get("data") or []
        if not isinstance(items, list):
            return []

        as_of = int(time.time())
        rows: list[Row] = []
        for rank, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            symbol = (item.get("underlying") or item.get("symbol") or "").strip()
            if not symbol:
                continue
            rows.append({
                "symbol":           symbol,
                "as_of":            as_of,
                "list_type":        list_type,
                "rank":             rank,
                "instrument":       item.get("instrumentType") or item.get("instrument"),
                "expiry":           item.get("expiryDate") or item.get("expiry"),
                "strike":           _f(item.get("strikePrice") or item.get("strike")),
                "option_type":      item.get("optionType"),
                "last_price":       _f(item.get("lastPrice") or item.get("ltp")),
                "pct_change":       _f(item.get("pChange") or item.get("perChange")),
                "contracts_traded": _i(item.get("contractsTraded") or item.get("contracts")),
                "value_lacs":       _f(item.get("valueInLakhs") or item.get("value")),
                "open_interest":    _i(item.get("openInterest")),
            })
        return rows


class MostActiveFnoByVolume(_MostActiveFnoBase):
    name = "most_active_fno_volume"
    list_type = "volume"


class MostActiveFnoByValue(_MostActiveFnoBase):
    name = "most_active_fno_value"
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