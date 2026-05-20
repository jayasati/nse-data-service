"""
Live large deals — bulk + block + short deals in one snapshot.

NSE endpoint: /api/snapshot-capital-market-largedeal
Returns six top-level arrays; three of them are the real data:
  BULK_DEALS_DATA   — bulk deals (single client > 0.5% of capital)
  BLOCK_DEALS_DATA  — block deals (single trade > ₹10cr / 5L shares)
  SHORT_DEALS_DATA  — short selling positions

Each gets tagged with deal_type ('bulk' | 'block' | 'short') in one table.

Fingerprint = sha256(deal_type | date | symbol | client_name | buy_sell | qty)[:16]
  - Two clients trading the same stock same day → distinct fingerprints.
  - Same trade re-reported across polls → deduped.

Used by Layer 6 institutional-flow signals (block_buy_institutional,
bulk_accumulation per §8.4).
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping, Sequence

from .base import EventCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"

# Which top-level keys hold which deal-type's rows
DEAL_BLOCKS = {
    "BULK_DEALS_DATA":  "bulk",
    "BLOCK_DEALS_DATA": "block",
    "SHORT_DEALS_DATA": "short",
}


class LargeDeals(EventCollector):
    name = "large_deals"
    table = "raw_large_deals"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/snapshot-capital-market-largedeal",
            referer=f"{NSE_BASE}/market-data/large-deals",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        now = int(time.time())
        rows: list[Row] = []
        for block_key, deal_type in DEAL_BLOCKS.items():
            items = data.get(block_key) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                symbol = (item.get("symbol") or "").strip()
                if not symbol:
                    continue
                rows.append({
                    "deal_type":          deal_type,
                    "deal_date":          item.get("date"),
                    "symbol":             symbol,
                    "company_name":       item.get("name"),
                    "client_name":        item.get("clientName"),
                    "buy_sell":           item.get("buySell"),
                    "quantity":           _i(item.get("qty")),
                    "weighted_avg_price": _f(item.get("watp")),
                    "remarks":            item.get("remarks"),
                    "created_at":         now,
                })
        return rows

    def fingerprint(self, row: Row) -> str:
        key = (
            f"{row['deal_type']}|{row.get('deal_date') or ''}|"
            f"{row['symbol']}|{row.get('client_name') or ''}|"
            f"{row.get('buy_sell') or ''}|{row.get('quantity') or ''}"
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


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