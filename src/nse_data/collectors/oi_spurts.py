"""
OI spurts collector — snapshot of OI activity for F&O underlyings.

NSE's response shape (confirmed 2026-05-19):
    symbol            - underlying ticker
    latestOI          - current open interest (contracts)
    prevOI            - prior session's closing OI
    changeInOI        - absolute change in contracts (latest - prev)
    avgInOI           - % change in OI vs avg (NSE's own basis, unspecified)
    volume            - cumulative volume in contracts today
    underlyingValue   - current spot price of the underlying

Note: NSE does NOT provide price change here. The "buildup pattern"
(long_buildup / short_buildup / etc. per §8.1) requires price direction,
so it's computed in Layer 4 by joining against raw_equity_quotes — not
in this collector. Layer 2 stores what NSE returns; nothing more.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"


class OiSpurts(SnapshotCollector):
    name = "oi_spurts"
    table = "raw_oi_spurts"
    pk_cols = ("symbol", "as_of")

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/live-analysis-oi-spurts-underlyings",
            referer=f"{NSE_BASE}/market-data/oi-spurts",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if isinstance(data, dict):
            data = data.get("data") or []
        if not isinstance(data, list):
            return []

        # One timestamp for the whole batch — every row in this poll shares
        # as_of, which makes "what did the market look like at 11:05?" trivial.
        as_of = int(time.time())

        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            if not symbol:
                continue

            rows.append({
                "symbol": symbol,
                "as_of": as_of,
                "latest_oi": _safe_int(item.get("latestOI")),
                "prev_oi": _safe_int(item.get("prevOI")),
                "change_in_oi": _safe_int(item.get("changeInOI")),
                "avg_oi_pct": _safe_float(item.get("avgInOI")),
                "volume": _safe_int(item.get("volume")),
                "underlying_value": _safe_float(item.get("underlyingValue")),
            })
        return rows


def _safe_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None