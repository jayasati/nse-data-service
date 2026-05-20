"""
Quarterly financial results filings.

NSE endpoint: /api/corporates-financial-results?index=equities&period=Quarterly
Returns the FULL historical archive (~3,800 rows). Dedup keeps the cost
amortized — only NEW filings actually persist.

Fingerprint = sha256(symbol | period | relatingTo | filingDate)[:16]
  - Same company filing different quarters → different fingerprints.
  - Same filing re-broadcast at a later time → same fingerprint, deduped.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping, Sequence

from .base import EventCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"


class FinancialResults(EventCollector):
    name = "financial_results"
    table = "raw_financial_results"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/corporates-financial-results",
            params={"index": "equities", "period": "Quarterly"},
            referer=f"{NSE_BASE}/companies-listing/corporate-filings-financial-results",
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
            filing_date = (item.get("filingDate") or "").strip()
            if not symbol or not filing_date:
                continue

            rows.append({
                "symbol":         symbol,
                "company_name":   item.get("companyName"),
                "industry":       _none_if_dash(item.get("industry")),
                "period":         item.get("period"),
                "relating_to":    item.get("relatingTo"),
                "financial_year": item.get("financialYear"),
                "from_date":      item.get("fromDate"),
                "to_date":        item.get("toDate"),
                "audited":        item.get("audited"),
                "cumulative":     item.get("cumulative"),
                "ind_as":         item.get("indAs"),
                "re_ind":         item.get("reInd"),
                "bank":           item.get("bank"),
                "filing_date":    filing_date,
                "seq_number":     item.get("seqNumber"),
                "old_new_flag":   item.get("oldNewFlag"),
                "xbrl_url":       _none_if_dash_url(item.get("xbrl")),
                "result_url":     _none_if_dash_url(item.get("resultD")),
                "params":         item.get("params"),
                "format":         item.get("format"),
                "created_at":     now,
            })
        return rows

    def fingerprint(self, row: Row) -> str:
        key = (
            f"{row['symbol']}|{row.get('period') or ''}|"
            f"{row.get('relating_to') or ''}|{row['filing_date']}"
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _none_if_dash(v):
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "-") else s


def _none_if_dash_url(v):
    """Some URL fields are placeholders ending in '-'; treat as missing."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # E.g. "https://nsearchives.nseindia.com/corporate/xbrl/-"
    if s.endswith("/-") or s == "-":
        return None
    return s