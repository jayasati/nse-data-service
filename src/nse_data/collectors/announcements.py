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

    # NSE serves every announcement segment from one endpoint, switched by
    # `index`. Subclasses override these two to cover SME / debt / MF / etc.
    # `segment` is stamped on each row; `index` is the query param. Both
    # default to equities so the base class is the equity collector as before.
    index: str = "equities"
    segment: str = "equities"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/corporate-announcements",
            params={"index": self.index},
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
                "segment": self.segment,
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


class SmeAnnouncements(Announcements):
    """SME (NSE Emerge) corporate announcements.

    Identical shape to the equity feed and every SME symbol is unique in NSE's
    exchange-wide symbol namespace, so the inherited symbol|subject|broadcast_dt
    fingerprint stays collision-safe — no need to fold `segment` in. Rows land
    in raw_announcements with segment='sme' and flow through the same Layer 3
    PDF pipeline as equity announcements.
    """
    name = "announcements_sme"
    index = "sme"
    segment = "sme"


class _NonEquityAnnouncements(EventCollector):
    """Base for non-equity announcement segments (debt, mf, …).

    Same `/api/corporate-announcements` endpoint as the equity feed, switched
    by `index`, but a different shape and destination:

      - `symbol` is usually null (debt issuers have none; mf rows are mostly
        funds), so these can't go in raw_announcements (symbol NOT NULL). They
        land in raw_nonequity_announcements, tagged by `segment`. mf ETF rows
        that *do* carry a symbol keep it.
      - Low signal for an equity bot, so metadata-only: attachment_url is kept
        but rows are NOT routed through the Layer 3 PDF pipeline.

    Fingerprint is a content tuple, not seq_id alone: the mf feed reuses one
    seq_id across an ETF-tagged and an untagged variant of the same disclosure,
    so seq_id isn't a reliable key. The tuple is stable per NSE row, so re-polls
    dedup and genuinely-distinct rows stay distinct.
    """
    table = "raw_nonequity_announcements"

    index: str = ""      # subclass sets — query param
    segment: str = ""    # subclass sets — stamped on each row + fingerprint

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/corporate-announcements",
            params={"index": self.index},
            referer=f"{NSE_BASE}/companies-listing/corporate-filings-announcements",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if isinstance(data, dict):
            data = data.get("data") or data.get("rows") or []
        if not isinstance(data, list):
            return []

        now = int(time.time())
        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            subject = (item.get("desc") or item.get("sm_desc") or "").strip()
            broadcast_dt = (
                item.get("an_dt")
                or item.get("sort_date")
                or item.get("exchdisstime")
                or ""
            ).strip()
            # No symbol requirement — these segments are mostly symbol-less.
            if not subject or not broadcast_dt:
                continue

            symbol = (item.get("symbol") or "").strip() or None
            rows.append({
                "segment":          self.segment,
                "symbol":           symbol,
                "seq_id":           item.get("seq_id"),
                "company_name":     item.get("sm_name") or item.get("companyName"),
                "isin":             item.get("sm_isin"),
                "subject":          subject,
                "details":          item.get("attchmntText"),
                "attachment_url":   _absolute_pdf_url(item.get("attchmntFile")),
                "broadcast_dt":     broadcast_dt,
                "receipt_dt":       item.get("dt"),
                "dissemination_dt": item.get("dissemDT"),
                "orgid":            item.get("orgid"),
                "created_at":       now,
            })
        return rows

    def fingerprint(self, row: Row) -> str:
        key = "|".join([
            self.segment,
            row.get("seq_id") or "",
            row.get("symbol") or "",
            row.get("company_name") or "",
            row["subject"],
            row["broadcast_dt"],
            row.get("attachment_url") or "",
        ])
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class DebtAnnouncements(_NonEquityAnnouncements):
    """Debt-segment announcements (NCDs, commercial paper, bonds)."""
    name = "announcements_debt"
    index = "debt"
    segment = "debt"


class MfAnnouncements(_NonEquityAnnouncements):
    """Mutual-fund + ETF announcements."""
    name = "announcements_mf"
    index = "mf"
    segment = "mf"


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