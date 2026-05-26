"""
SEBI Integrated Filing disclosures (financials + governance).

NSE endpoint: /api/integrated-filing-results?type=<type>&size=<n>

Two filing types are served separately by the `type` param; plan() issues one
request per type. Each is a large newest-first archive (~20k rows), so we pull
the latest `page_size` per type on a weekly cadence and let dedup absorb the
overlap with the previous run — the same "fetch archive, dedup" pattern as the
financial_results collector.

Fingerprint = filing_type|seq_id. seq_id is unique within a type; the type
prefix guards against the two types' shared id space colliding.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping, Sequence

from .base import EventCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"
FILING_TYPES = (
    "Integrated Filing- Financials",
    "Integrated Filing- Governance",
)


class IntegratedFilings(EventCollector):
    name = "integrated_filings"
    table = "raw_integrated_filings"

    # How many of the latest filings to pull per type per run. A week's worth
    # of new integrated filings sits comfortably under this outside peak results
    # season; raise it if the weekly run starts missing the tail. Tunable knob.
    page_size: int = 500

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/integrated-filing-results",
            params={"type": ftype, "size": str(self.page_size)},
            referer=f"{NSE_BASE}/companies-listing/corporate-filings-integrated-filing",
            response_type="json",
            meta={"filing_type": ftype},
        ) for ftype in FILING_TYPES]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if isinstance(data, dict):
            items = data.get("data") or []
        else:
            items = data
        if not isinstance(items, list):
            return []

        now = int(time.time())
        rows: list[Row] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            seq_id = item.get("seq_Id")
            filing_type = item.get("type")
            # seq_id + type form the identity; without them we can't fingerprint.
            if not seq_id or not filing_type:
                continue

            rows.append({
                "seq_id":          str(seq_id),
                "filing_type":     filing_type,
                "type_sub":        item.get("type_Sub"),
                "symbol":          (item.get("symbol") or "").strip() or None,
                "company_name":    item.get("smName") or item.get("cmName"),
                "qe_date":         item.get("qe_Date"),
                "audited":         item.get("audited"),
                "consolidated":    item.get("consolidated"),
                "ixbrl_url":       item.get("ixbrl"),
                "xbrl_url":        item.get("xbrl"),
                "pdf_url":         _clean_url(item.get("pdf_attach")),
                "broadcast_dt":    item.get("broadcast_Date"),
                "revised_dt":      item.get("revised_Date"),
                "revision_remark": item.get("revision_Remark"),
                "creation_dt":     item.get("creation_Date"),
                "created_at":      now,
            })
        return rows

    def fingerprint(self, row: Row) -> str:
        key = f"{row['filing_type']}|{row['seq_id']}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _clean_url(v: str | None) -> str | None:
    """NSE emits a placeholder '.../corporate/null' when there's no attachment."""
    if not v:
        return None
    v = v.strip()
    if not v or v.endswith("/null"):
        return None
    return v
