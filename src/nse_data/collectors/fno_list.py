"""
F&O eligible securities — the ~209 stocks that have futures/options listed.

NSE endpoint: /api/equity-stock-indices?index=SECURITIES IN F&O
(renamed from /api/equity-stockIndices around 2026-05-22; old path 404s.)
ReferenceCollector with diff_upsert. Weekly cadence. When a stock joins or
leaves F&O (NSE rebalances every 6 months), diff catches the changes.

Used by Layer 6 to filter signals to F&O-eligible names only.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import ReferenceCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"


class FnoList(ReferenceCollector):
    name = "fno_list"
    table = "raw_fno_list"
    key_cols = ("symbol",)
    replace_strategy = "diff"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/equity-stock-indices",
            params={"index": "SECURITIES IN F&O"},
            referer=f"{NSE_BASE}/market-data/live-equity-market",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        items = data.get("data") or []
        if not isinstance(items, list):
            return []

        now = int(time.time())
        rows: list[Row] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            if not symbol:
                continue
            # Skip the index-header row (same pattern as live_equity)
            if symbol.upper() == "SECURITIES IN F&O":
                continue
            rows.append({
                "symbol":     symbol,
                "series":     item.get("series"),
                "last_price": _f(item.get("lastPrice")),
                "fetched_at": now,
            })
        return rows


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None