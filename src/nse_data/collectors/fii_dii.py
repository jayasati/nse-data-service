"""
FII/DII daily net flows.

NSE endpoint: /api/fiidiiTradeReact
Returns ~2 rows: FII Cash Market net, DII Cash Market net.

Daily cadence (architecture §5.8 #57). 18:30 IST publication time per NSE.
This is a JSON endpoint, not a CSV — different from the other Day 4
collectors but lands in the same EOD bucket.

The endpoint serves only cash-market totals. F&O institutional flow is in
a separate report not yet wired (defer to LEARNINGS).
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"


class FiiDii(SnapshotCollector):
    name = "fii_dii"
    table = "raw_fii_dii"
    pk_cols = ("date", "category")

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/fiidiiTradeReact",
            referer=f"{NSE_BASE}/reports/fii-dii",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, list):
            return []
        now = int(time.time())
        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            category = (item.get("category") or "").strip()
            date_str = (item.get("date") or "").strip()
            if not category or not date_str:
                continue
            rows.append({
                "date":       date_str,
                "category":   category,
                "buy_value":  _f(item.get("buyValue")),
                "sell_value": _f(item.get("sellValue")),
                "net_value":  _f(item.get("netValue")),
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