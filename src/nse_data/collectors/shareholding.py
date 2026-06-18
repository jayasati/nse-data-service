"""
Shareholding pattern master — per-symbol ownership split.

NSE endpoint: /api/corporate-share-holdings-master?index=<equities|sme>
  (note the hyphen in "share-holdings"; the older "shareholdings" path 404s.)

Returns one row per symbol with the latest quarter's headline split: promoter +
promoter-group %, public %, employee-trust %, plus the quarter-end date, ISIN
and the XBRL link to the full filing. Feeds Layer 5 fundamentals (promoter
holding / public split) and Layer 6 ownership checks.

ReferenceCollector (diff_upsert, key=symbol): the current master IS the truth.
plan() pulls both segments (equities + sme); persist() diffs the merged set
against the table, so a promoter_pct change shows up as 'updated' and a
delisting as 'removed'. No per-run timestamp is stored, so the diff reflects
genuine quarterly changes rather than churning every row each run.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .base import ReferenceCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"
SHP_REFERER = f"{NSE_BASE}/companies-listing/corporate-filings-shareholding-pattern"


class ShareholdingPattern(ReferenceCollector):
    name = "shareholding_pattern"
    table = "raw_shareholding_pattern"
    key_cols = ("symbol",)
    replace_strategy = "diff"

    # Segments fetched and merged into one master. Tests narrow this to a single
    # index so the two same-path requests don't feed diff_upsert duplicate keys.
    indexes: tuple[str, ...] = ("equities", "sme")

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/corporate-share-holdings-master",
            params={"index": idx},
            referer=SHP_REFERER,
            response_type="json",
            meta={"segment": idx},
        ) for idx in self.indexes]

    def persist(self, db, rows):
        # equities+sme (and revised filings) can repeat a symbol across the merged
        # batch → UNIQUE(symbol) fails. Dedup by symbol (last wins) before upsert.
        seen: dict = {}
        for r in rows:
            seen[r["symbol"]] = r
        return super().persist(db, list(seen.values()))

    def normalize(self, data: Any, request: Request) -> list[Row]:
        # Endpoint returns a bare list; tolerate a dict-wrapped variant too.
        if isinstance(data, dict):
            data = data.get("data") or data.get("rows") or []
        if not isinstance(data, list):
            return []

        segment = (request.meta or {}).get("segment")
        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            if not symbol:
                continue
            rows.append({
                "symbol":             symbol,
                "segment":            segment,
                "company_name":       item.get("name"),
                "industry":           _dash(item.get("industry")),
                "isin":               item.get("isin"),
                "qe_date":            item.get("date"),
                "promoter_pct":       _f(item.get("pr_and_prgrp")),
                "public_pct":         _f(item.get("public_val")),
                "employee_trust_pct": _f(item.get("employeeTrusts")),
                "record_id":          item.get("recordId"),
                "xbrl_url":           item.get("xbrl"),
                "revised":            item.get("revisedData"),
                "submission_date":    item.get("submissionDate"),
                "broadcast_dt":       item.get("broadcastDate"),
            })
        return rows


def _dash(v):
    """NSE's "-" placeholder -> None."""
    if v is None:
        return None
    v = str(v).strip()
    return None if v in ("", "-") else v


def _f(v):
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
