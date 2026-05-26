"""
Pre-open session snapshot — IEP and gap signal per security.

NSE endpoint: /api/market-data-pre-open?key=ALL

The pre-open call auction runs 09:00–09:15 IST. We poll once at ~09:08, after
the book has settled but before continuous trading. Each security's response
carries two blocks:

  metadata           - headline IEP / change / prev close / 52w range
  detail.preOpenMarket - order-book aggregates (buy/sell qty, ATO qty, IEP)

The gap-detection signal the bot cares about is IEP vs previousClose, already
surfaced by NSE as change / pChange. We store per-symbol rows; the response's
market-wide breadth (advances/declines) is a different grain and not stored.

Snapshot semantics (archetype A): rows are keyed by (symbol, as_of), so a
same-session re-poll is a no-op and polling on later days accumulates history.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"
PRE_OPEN_REFERER = f"{NSE_BASE}/market-data/pre-open-market-cm-and-emerge-market"


class PreOpen(SnapshotCollector):
    name = "pre_open"
    table = "raw_pre_open"
    pk_cols = ("symbol", "as_of")

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/market-data-pre-open",
            params={"key": "ALL"},
            referer=PRE_OPEN_REFERER,
            response_type="json",
            meta={},
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        items = data.get("data") or []
        if not isinstance(items, list):
            return []

        as_of = int(time.time())
        nse_timestamp = data.get("timestamp")

        rows: list[Row] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            md = item.get("metadata") or {}
            pom = (item.get("detail") or {}).get("preOpenMarket") or {}

            symbol = (md.get("symbol") or "").strip()
            if not symbol:
                continue

            rows.append({
                "symbol":              symbol,
                "as_of":               as_of,
                "series":              md.get("series"),
                # IEP lives in both blocks; metadata is always present, detail
                # is the authoritative book value — prefer metadata, fall back.
                "iep":                 _f(_first(md.get("iep"), pom.get("IEP"))),
                "final_price":         _f(pom.get("finalPrice")),
                "prev_close":          _f(md.get("previousClose")),
                "change":              _f(md.get("change")),
                "pct_change":          _f(md.get("pChange")),
                "final_quantity":      _i(_first(pom.get("finalQuantity"),
                                                 md.get("finalQuantity"))),
                "total_traded_volume": _i(pom.get("totalTradedVolume")),
                "total_turnover":      _f(md.get("totalTurnover")),
                "total_buy_qty":       _i(pom.get("totalBuyQuantity")),
                "total_sell_qty":      _i(pom.get("totalSellQuantity")),
                "ato_buy_qty":         _i(pom.get("atoBuyQty")),
                "ato_sell_qty":        _i(pom.get("atoSellQty")),
                "year_high":           _f(md.get("yearHigh")),
                "year_low":            _f(md.get("yearLow")),
                "nse_timestamp":       nse_timestamp,
            })
        return rows


def _first(*vals):
    """First non-None value — handles NSE putting a field in one block only."""
    for v in vals:
        if v is not None:
            return v
    return None


def _f(v):
    """Coerce to float; NSE's "-" / "" / None NULL conventions become None."""
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    if v is None or v == "" or v == "-":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
