"""
Today's newly-listed stocks.

NSE endpoint: /api/new-listing-today
EventCollector with fingerprint dedup. Daily morning cadence.

NSE returns empty body on most days (no new listings). Empty response
must not crash — our normalize() handles both empty bodies and
malformed JSON gracefully.

Architecture §5.7 #53: stocks here auto-blacklist for 30 days. That
blacklist logic lives in Layer 6, not here — we just capture the listings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Mapping, Sequence

from .base import EventCollector, Request, Row


log = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"


class NewListings(EventCollector):
    name = "new_listings"
    table = "raw_new_listings"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/new-listing-today",
            referer=f"{NSE_BASE}/market-data/new-stock-exchange-listings-recent",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        # NSE returns empty body / non-JSON on days with no listings.
        # The session manager may have already raised; if not, data
        # could be None, empty dict, empty list. Handle all:
        if not data:
            return []

        # Sometimes wrapped in a 'data' key
        if isinstance(data, dict):
            data = data.get("data") or data.get("listings") or []
        if not isinstance(data, list):
            return []

        now = int(time.time())
        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            listing_date = (item.get("listingDate") or item.get("date") or "").strip()
            if not symbol:
                continue

            rows.append({
                "symbol":       symbol,
                "company_name": item.get("companyName") or item.get("name"),
                "series":       item.get("series"),
                "listing_date": listing_date,
                "isin":         item.get("isin"),
                "issue_price":  _f(item.get("issuePrice") or item.get("price")),
                "market_lot":   _i(item.get("marketLot") or item.get("lotSize")),
                "face_value":   _f(item.get("faceValue")),
                "created_at":   now,
            })
        return rows

    def fingerprint(self, row: Row) -> str:
        key = f"{row['symbol']}|{row.get('listing_date') or ''}"
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