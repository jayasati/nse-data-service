"""
India VIX with expected-range (1σ / 2σ daily envelope) computation.

India VIX is NSE's volatility index — the market's expectation of 30-day
annualized Nifty volatility, in percent. NSE serves it inside the same
`/api/allIndices` payload that the `indices` collector reads, alongside the
NIFTY 50 spot. We hit that endpoint here too (separate endpoint_name → its own
rate-limit/circuit bucket; one extra call per poll) and, on every poll, derive
the expected daily move and the 1σ / 2σ price envelopes around the Nifty spot.

Envelope math (research Pillar: VIX-implied range drives stop/target sizing):
    VIX is annualized vol in %. Scaling to one trading session:
        expected_move_pct (1σ, daily) = VIX / sqrt(TRADING_DAYS)
        expected_move_pts             = nifty_spot * expected_move_pct / 100
        sigmaN_upper/lower            = nifty_spot ± N * expected_move_pts
    TRADING_DAYS = 252 (annualized-to-trading-day convention). Switch to 365 if
    a calendar-day envelope is ever preferred — `expected_move_pct` is stored
    raw so downstream can rescale without re-fetching.

This is an NSE-session collector (unlike macro/gift_nifty), so it uses the
normal Collector pipeline; only normalize() carries the derived columns.
"""

from __future__ import annotations

import math
import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector

NSE_BASE = "https://www.nseindia.com"

# Annualized-to-daily scaling. Trading days, not calendar days — the standard
# convention for collapsing an annualized vol figure into a one-session move.
TRADING_DAYS = 252
_SQRT_TRADING_DAYS = math.sqrt(TRADING_DAYS)


class IndiaVix(SnapshotCollector):
    name = "india_vix"
    table = "raw_india_vix"
    pk_cols = ("as_of",)

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/allIndices",
            referer=f"{NSE_BASE}/market-data/live-market-indices",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []

        vix_item = _find_index(data, "INDIA VIX")
        if vix_item is None:
            # No VIX row → nothing to compute. Don't fabricate a row.
            return []

        vix = _f(vix_item.get("last"))
        if vix is None:
            return []

        # The envelope anchor. Absent NIFTY 50, we still record the VIX + the
        # expected-move percentage; the point envelopes stay NULL.
        nifty_item = _find_index(data, "NIFTY 50")
        nifty_spot = _f(nifty_item.get("last")) if nifty_item else None

        # 1σ daily move as a percentage of spot.
        expected_move_pct = round(vix / _SQRT_TRADING_DAYS, 4)

        sigma1_upper = sigma1_lower = sigma2_upper = sigma2_lower = None
        if nifty_spot is not None:
            move_pts = nifty_spot * expected_move_pct / 100
            sigma1_upper = round(nifty_spot + move_pts, 2)
            sigma1_lower = round(nifty_spot - move_pts, 2)
            sigma2_upper = round(nifty_spot + 2 * move_pts, 2)
            sigma2_lower = round(nifty_spot - 2 * move_pts, 2)

        as_of = int(time.time())
        return [{
            "as_of":             as_of,
            "vix":               vix,
            "vix_open":          _f(vix_item.get("open")),
            "vix_high":          _f(vix_item.get("high")),
            "vix_low":           _f(vix_item.get("low")),
            "vix_prev_close":    _f(vix_item.get("previousClose")),
            "vix_pct_change":    _f(vix_item.get("percentChange")),
            "nifty_spot":        nifty_spot,
            "expected_move_pct": expected_move_pct,
            "sigma1_upper":      sigma1_upper,
            "sigma1_lower":      sigma1_lower,
            "sigma2_upper":      sigma2_upper,
            "sigma2_lower":      sigma2_lower,
            "nse_timestamp":     data.get("timestamp"),
            "captured_at":       as_of,
        }]


def _find_index(data: dict, name: str) -> dict | None:
    """Locate one index row in the allIndices payload by name/symbol."""
    target = name.strip().upper()
    for item in data.get("data") or []:
        if not isinstance(item, dict):
            continue
        sym = (item.get("indexSymbol") or item.get("index") or "").strip().upper()
        if sym == target:
            return item
    return None


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
