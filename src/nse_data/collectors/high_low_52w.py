"""
52-week high/low list.

NSE endpoint: /api/live-analysis-52Week?index={high|low}

The response splits results by stock price:
  dataLtpGreater20 — stocks priced > ₹20 (the meaningful breakouts)
  dataLtpLess20    — penny stocks priced ≤ ₹20 (mostly noise but stored)

Both arrays have identical schema. We tag each row with price_tier so Layer 6
can filter — a 52w high in a ₹500 stock means more than in a ₹15 penny.

Two YAML instances run (high + low). NSE returns up to ~80 rows per side
across both tiers.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"


class _HighLow52WBase(SnapshotCollector):
    table = "raw_high_low_52w"
    pk_cols = ("symbol", "event", "price_tier", "as_of")

    # Subclass sets:
    event: str = ""        # 'high' | 'low'
    nse_param: str = ""    # 'high' | 'low'

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/live-analysis-52Week",
            params={"index": self.nse_param},
            referer=f"{NSE_BASE}/market-data/52-week-high-low-equity-market",
            response_type="json",
            meta={"event": self.event},
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        as_of = int(time.time())
        event = (request.meta or {}).get("event", self.event)

        rows: list[Row] = []
        for tier_key, tier_tag in (
            ("dataLtpGreater20", "gt20"),
            ("dataLtpLess20",    "lte20"),
        ):
            items = data.get(tier_key) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                symbol = (item.get("symbol") or "").strip()
                if not symbol:
                    continue
                rows.append({
                    "symbol":          symbol,
                    "as_of":           as_of,
                    "event":           event,
                    "price_tier":      tier_tag,
                    # NSE's typo "comapnyName" is in the wire format — keep as-is.
                    "company_name":    item.get("comapnyName") or item.get("companyName"),
                    "new_52w_level":   _f(item.get("new52WHL")),
                    "prev_52w_level":  _f(item.get("prev52WHL")),
                    "prev_hl_date":    item.get("prevHLDate"),
                    "ltp":             _f(item.get("ltp")),
                    "prev_close":      _f(item.get("prevClose")),
                    "change":          _f(item.get("change")),
                    "pct_change":      _f(item.get("pChange") or item.get("perChange")),
                })
        return rows


class High52W(_HighLow52WBase):
    name = "high_52w"
    event = "high"
    nse_param = "high"


class Low52W(_HighLow52WBase):
    name = "low_52w"
    event = "low"
    nse_param = "low"


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None