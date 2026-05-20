"""
Insider trading (SEBI PIT) filings — promoter and insider buy/sell.

NSE endpoint: /api/corporates-pit?index=equities
Returns a list of recent insider transactions. Often empty during off-hours
(probed 20-May-2026 16:00 IST: 0 rows). Worth collecting anyway — the moment
a real filing happens, this catches it.

Fingerprint = sha256(symbol | acquirer | period_to | type | qty)[:16]

Field shape is speculative based on architecture §5.4 #35 + historical
schema. May need adjustment when real filings land. Both 'data' wrapper
and bare list payloads are accepted.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping, Sequence

from .base import EventCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"


class InsiderTrading(EventCollector):
    name = "insider_trading"
    table = "raw_insider_trading"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/corporates-pit",
            params={"index": "equities"},
            referer=f"{NSE_BASE}/companies-listing/corporate-filings-insider-trading",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        # NSE wraps under 'data' key, but also returns a bare list sometimes
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
            if not symbol:
                continue

            rows.append({
                "symbol":              symbol,
                "company_name":        item.get("company") or item.get("companyName"),
                "acquirer_name":       item.get("acqName"),
                "acquirer_category":   item.get("personCategory"),
                "securities_type":     item.get("secType"),
                "transaction_type":    item.get("tdpTransactionType"),
                "no_of_securities":    _i(item.get("secAcq")),
                "value_in_rupees":     _f(item.get("secVal")),
                "holding_before":      _i(item.get("befAcqSharesNo")),
                "holding_after":       _i(item.get("afterAcqSharesNo")),
                "period_from":         item.get("acquisitionFrom"),
                "period_to":           item.get("acquisitionTo"),
                "intimation_date":     item.get("tdpDate") or item.get("date"),
                "mode_of_acquisition": item.get("acquisitionMode"),
                "derivative_contract": item.get("derivativeType"),
                "attachment_url":      item.get("xbrl") or item.get("attchmntFile"),
                "created_at":          now,
            })
        return rows

    def fingerprint(self, row: Row) -> str:
        key = (
            f"{row['symbol']}|{row.get('acquirer_name') or ''}|"
            f"{row.get('period_to') or ''}|{row.get('transaction_type') or ''}|"
            f"{row.get('no_of_securities') or ''}"
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