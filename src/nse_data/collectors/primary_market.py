"""
Upcoming primary-market issues: IPO, OFS, rights, NCDs.

NSE endpoint: /api/all-upcoming-issues?category={ipo|ofs|rights|debt}
Four categories, one collector. We do four sequential fetches and tag
each row with its issue_type.

EventCollector pattern with fingerprint dedup: same issue may appear
across daily polls until it's announced/closed.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping, Sequence

from .base import EventCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"

CATEGORIES = ("ipo", "ofs", "rights", "debt")


class PrimaryMarket(EventCollector):
    name = "primary_market"
    table = "raw_primary_issues"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [
            Request(
                path_or_url="/api/all-upcoming-issues",
                params={"category": cat},
                referer=f"{NSE_BASE}/market-data/upcoming-issues",
                response_type="json",
                meta={"issue_type": cat},
            )
            for cat in CATEGORIES
        ]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        # Empty dict {} means no upcoming issues in this category — valid.
        if isinstance(data, dict) and not data:
            return []

        # NSE returns either a bare list, or {data: [...]}, depending on category
        if isinstance(data, dict):
            data = data.get("data") or []
        if not isinstance(data, list):
            return []

        issue_type = (request.meta or {}).get("issue_type", "unknown")
        now = int(time.time())
        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            company = (item.get("companyName") or item.get("name") or "").strip()
            open_date = (
                item.get("issueStartDate")
                or item.get("openDate")
                or item.get("startDate")
                or ""
            ).strip()
            # NCDs may have no symbol — keep them if company_name is set
            if not symbol and not company:
                continue

            rows.append({
                "issue_type":   issue_type,
                "symbol":       symbol or None,
                "company_name": company,
                "series":       item.get("series"),
                "issue_size":   _f(item.get("issueSize")),
                "issue_price":  _f(item.get("issuePrice")),
                "price_band":   item.get("priceBand"),
                "open_date":    open_date or None,
                "close_date":   item.get("issueEndDate") or item.get("closeDate"),
                "listing_date": item.get("listingDate"),
                "lot_size":     _i(item.get("lotSize") or item.get("marketLot")),
                "status":       item.get("status"),
                "created_at":   now,
            })
        return rows

    def fingerprint(self, row: Row) -> str:
        key = (
            f"{row['issue_type']}|{row.get('symbol') or ''}|"
            f"{row.get('company_name') or ''}|{row.get('open_date') or ''}"
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