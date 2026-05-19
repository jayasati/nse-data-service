"""
Corporate announcements collector (equity segment).

Fingerprint = sha256(symbol | subject | broadcast_dt)[:16]
  symbol+subject+broadcast_dt is the minimal triple NSE guarantees unique
  per filing. Truncated to 16 hex chars — collision-resistant enough for the
  ~1M announcements/year throughput, and short enough to grep.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping, Sequence

from .base import EventCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"


class Announcements(EventCollector):
    name = "announcements_equity"
    table = "raw_announcements"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/corporate-announcements",
            params={"index": "equities"},
            referer=f"{NSE_BASE}/companies-listing/corporate-filings-announcements",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        # NSE has been observed to return either a bare list or a dict
        # wrapping the list under "data" or "rows". Handle all three.
        if isinstance(data, dict):
            data = data.get("data") or data.get("rows") or []
        if not isinstance(data, list):
            return []

        now = int(time.time())
        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue

            symbol = (item.get("symbol") or "").strip()
            subject = (item.get("desc") or item.get("sm_desc") or "").strip()
            broadcast_dt = (
                item.get("an_dt")
                or item.get("sort_date")
                or item.get("exchdisstime")
                or ""
            ).strip()

            # Skip malformed rows — surfaced in unit tests against fixtures
            if not symbol or not subject or not broadcast_dt:
                continue

            rows.append({
                "segment": "equities",
                "symbol": symbol,
                "company_name": item.get("sm_name") or item.get("companyName"),
                "subject": subject,
                "details": item.get("attchmntText"),
                "attachment_url": _absolute_pdf_url(item.get("attchmntFile")),
                "broadcast_dt": broadcast_dt,
                "receipt_dt": item.get("dt"),
                "dissemination_dt": item.get("dissemDT"),
                # Layer 3 fills these:
                "priority": None,
                "pdf_path": None,
                "pdf_text": None,
                "extracted": None,
                "sentiment": None,
                "pdf_status": "pending",
                "retention_policy": None,
                "deleted_at": None,
                "created_at": now,
            })
        return rows

    def fingerprint(self, row: Row) -> str:
        key = f"{row['symbol']}|{row['subject']}|{row['broadcast_dt']}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _absolute_pdf_url(rel: str | None) -> str | None:
    """NSE returns PDF paths as relative URLs. Make absolute so Layer 3 can fetch."""
    if not rel:
        return None
    rel = rel.strip()
    if not rel:
        return None
    if rel.startswith(("http://", "https://")):
        return rel
    if not rel.startswith("/"):
        rel = "/" + rel
    return f"{NSE_BASE}{rel}"