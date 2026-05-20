"""
Upcoming board meetings — pre-announced corporate events.

NSE endpoint: /api/corporate-board-meetings?index=equities
Returns ~20 most recent board meeting intimations.

Fingerprint = sha256(symbol | meeting_date | purpose)[:16]
  - Same purpose + same date for same company = same meeting.
  - Different purpose (e.g. one company has two upcoming meetings) → distinct.

Used by Layer 5 events.calendar: pre-screening watchlist stocks before
their announced earnings/dividend meetings.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping, Sequence

from .base import EventCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"


class BoardMeetings(EventCollector):
    name = "board_meetings"
    table = "raw_board_meetings"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/corporate-board-meetings",
            params={"index": "equities"},
            referer=f"{NSE_BASE}/companies-listing/corporate-filings-board-meetings",
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
            symbol = (item.get("bm_symbol") or "").strip()
            meeting_date = (item.get("bm_date") or "").strip()
            purpose = (item.get("bm_purpose") or "").strip()
            if not symbol or not meeting_date or not purpose:
                continue

            rows.append({
                "symbol":         symbol,
                "meeting_date":   meeting_date,
                "purpose":        purpose,
                "details":        item.get("bm_desc"),
                # NSE typo "sm_indusrty" — see Day 2 LEARNINGS
                "industry":       item.get("sm_indusrty") or item.get("sm_industry"),
                "company_name":   item.get("sm_name"),
                "isin":           item.get("sm_isin"),
                "timestamp":      item.get("bm_timestamp"),
                "attachment_url": item.get("attachment"),
                "created_at":     now,
            })
        return rows

    def fingerprint(self, row: Row) -> str:
        key = f"{row['symbol']}|{row['meeting_date']}|{row['purpose']}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]