"""
Stocks in periodic call auction (illiquid securities) — daily membership list.

NSE endpoint: /api/live-watch-call-auction

NSE moves illiquid securities out of continuous trading and into a periodic
call auction (six windowed sessions through the day). Membership of that set
is the signal Layer 6 wants: a stock here is illiquid, so exclude it from
intraday signals.

This is a ReferenceCollector — the current response IS the truth. diff_upsert
keyed on `symbol` means a stock entering the set is `inserted`, leaving is
`removed` (the row is deleted), so the table always reflects "currently in
call auction." We pull once daily after the sessions close, so the per-session
price/qty columns hold the day's final auction record.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import ReferenceCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"
SCA_REFERER = f"{NSE_BASE}/market-data/stocks-in-call-auction"

_SESSIONS = range(1, 7)


class CallAuction(ReferenceCollector):
    name = "call_auction"
    table = "raw_call_auction"
    key_cols = ("symbol",)
    replace_strategy = "diff"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/live-watch-call-auction",
            referer=SCA_REFERER,
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        items = data.get("data") or []
        if not isinstance(items, list):
            return []

        now = int(time.time())
        rows: list[Row] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            if not symbol:
                continue

            row: Row = {
                "symbol":         symbol,
                "avg_price":      _f(item.get("avg_price")),
                "total_volume":   _i(item.get("total_volume")),
                "total_turnover": _f(item.get("total_turnover")),
                "captured_at":    now,
            }
            # A symbol can be in the auction set with no trades yet, in which
            # case the session_* fields are simply absent -> stored as NULL.
            for n in _SESSIONS:
                row[f"session{n}_price"] = _f(item.get(f"session{n}_price"))
                row[f"session{n}_qty"] = _i(item.get(f"session{n}_qty"))
            rows.append(row)
        return rows


def _f(v):
    """Coerce to float; NSE's "-" / "" / None NULL conventions become None.
    Note 0 is preserved (a real auction value), only the NULL sentinels drop."""
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
