"""
Corporate actions — dividends, splits, bonuses, rights issues, buybacks.

NSE endpoint: /api/corporates-corporateActions?index=equities
Returns ~20 recent corporate actions across all listed companies.

Fingerprint = sha256(symbol | subject | ex_date)[:16]
  - The same action may be re-broadcast; ex_date is the canonical anchor.
  - Subject is the human-readable summary ("Interim Dividend - Rs 10").
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping, Sequence

from .base import EventCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"


class CorporateActions(EventCollector):
    name = "corporate_actions"
    table = "raw_corporate_actions"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/corporates-corporateActions",
            params={"index": "equities"},
            referer=f"{NSE_BASE}/companies-listing/corporate-filings-actions",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if isinstance(data, dict):
            data = data.get("data") or []
        if not isinstance(data, list):
            return []

        now = int(time.time())
        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            subject = (item.get("subject") or "").strip()
            ex_date = (item.get("exDate") or "").strip()
            if not symbol or not subject:
                continue

            rows.append({
                "symbol":            symbol,
                "series":            item.get("series"),
                "industry":          item.get("ind"),
                "face_value":        _f(item.get("faceVal")),
                "subject":           subject,
                "ex_date":           _none_if_dash(ex_date),
                "record_date":       _none_if_dash(item.get("recDate")),
                "bc_start_date":     _none_if_dash(item.get("bcStartDate")),
                "bc_end_date":       _none_if_dash(item.get("bcEndDate")),
                "nd_start_date":     _none_if_dash(item.get("ndStartDate")),
                "nd_end_date":       _none_if_dash(item.get("ndEndDate")),
                "ca_broadcast_date": item.get("caBroadcastDate"),
                "company_name":      item.get("comp"),
                "isin":              item.get("isin"),
                "created_at":        now,
            })
        return rows

    def fingerprint(self, row: Row) -> str:
        # Use ex_date if present, otherwise fall back to subject+symbol alone
        ex = row.get("ex_date") or ""
        key = f"{row['symbol']}|{row['subject']}|{ex}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _f(v):
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _none_if_dash(v):
    """NSE writes '-' for missing date fields. Coerce to NULL."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "-") else s