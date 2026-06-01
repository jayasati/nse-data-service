"""
Live equity quotes — fetch LTP/volume for an index's constituents.

NSE endpoint: /api/equity-stock-indices?index=<NAME>
(NSE renamed this from the camelCase /api/equity-stockIndices around
2026-05-22; the old path now 404s. Response shape is unchanged.)

Returns ~50 stocks with one row per symbol containing LTP, OHLC of the day,
volume, value, 52-week high/low. The first row of the response is the index
itself (its `symbol` field equals the index name); we drop it because index
quotes live in raw_indices, not raw_equity_quotes.

This is a parameterized collector. The base class (LiveEquity) defaults to
NIFTY 50. To poll a different index, subclass and override index_name + name.

A subclass may also set `fetch_indices` to a tuple of NSE index names — the
collector then polls each, merges their constituents (deduped by symbol), and
stores them under the single canonical `index_name` label with one shared
as_of. LiveEquityTotalMarket uses this to union NIFTY 500 + NIFTY SMALLCAP 500
into the broad live universe, since NSE no longer serves NIFTY TOTAL MARKET.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"


class LiveEquity(SnapshotCollector):
    name = "live_equity_nifty50"
    table = "raw_equity_quotes"
    pk_cols = ("symbol", "index_name", "as_of")

    # Canonical label written to the index_name column (and matched by the
    # dashboard's LIVE_INDEX). Override in a subclass for other indices.
    index_name: str = "NIFTY 50"
    # NSE index name(s) to poll. Empty -> just (index_name,). When a subclass
    # lists several, their constituents are merged under the single index_name
    # label above. Kept separate from index_name because the stored label may
    # not be a real NSE index (e.g. a union of two).
    fetch_indices: tuple[str, ...] = ()

    def _fetch_indices(self) -> tuple[str, ...]:
        return self.fetch_indices or (self.index_name,)

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        # Stamp one timestamp for the whole run so a multi-index feed's rows
        # share an as_of — the dashboard pins its live view to a single
        # MAX(as_of), so mixed timestamps would hide half the universe.
        self._as_of = int(time.time())
        return [Request(
            path_or_url="/api/equity-stock-indices",
            params={"index": idx},
            referer=f"{NSE_BASE}/market-data/live-equity-market",
            response_type="json",
            meta={"fetch_index": idx},
        ) for idx in self._fetch_indices()]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        items = data.get("data") or []
        if not isinstance(items, list):
            return []

        as_of = getattr(self, "_as_of", None) or int(time.time())
        meta = request.meta or {}
        # Which NSE index this response came from — used only to drop its
        # index-self row. Back-compat: older callers passed "index_name".
        fetch_index = meta.get("fetch_index") or meta.get("index_name") or self.index_name
        # The index appears as the first row with symbol == fetch index.
        # Compare case-insensitively to be defensive about NSE variations.
        index_marker = fetch_index.strip().upper()

        rows: list[Row] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            if not symbol:
                continue
            # Drop the index-itself row — those quotes live in raw_indices.
            if symbol.upper() == index_marker:
                continue

            rows.append({
                "symbol":      symbol,
                "as_of":       as_of,
                "index_name":  self.index_name,   # canonical stored label
                "last_price":  _f(item.get("lastPrice")),
                "open":        _f(item.get("open")),
                "day_high":    _f(item.get("dayHigh")),
                "day_low":     _f(item.get("dayLow")),
                "prev_close":  _f(item.get("previousClose")),
                "change":      _f(item.get("change")),
                "pct_change":  _f(item.get("pChange")),
                "volume":      _i(item.get("totalTradedVolume")),
                "value":       _f(item.get("totalTradedValue")),
                "year_high":   _f(item.get("yearHigh")),
                "year_low":    _f(item.get("yearLow")),
            })
        return rows

    def persist(self, db, rows: list[Row]):
        # Multi-index feeds overlap (NIFTY 500 ∩ NIFTY SMALLCAP 500 ≈ 250
        # names), and every row in a run shares one as_of, so an overlapping
        # symbol would collide on the PK (symbol, index_name, as_of). Collapse
        # to one row per symbol first; the quote is identical across indices.
        if len(self._fetch_indices()) > 1:
            rows = list({r["symbol"]: r for r in rows}.values())
        return super().persist(db, rows)


class LiveEquityTotalMarket(LiveEquity):
    """Live quotes for the broad market — NIFTY 500 unioned with NIFTY SMALLCAP
    500 (~755 distinct stocks), polled at 1-min cadence during market hours to
    power the dashboard's live broad-market view.

    Was a single "NIFTY TOTAL MARKET" call (~750 stocks), but NSE stopped
    serving that index's constituents on /api/equity-stock-indices around
    2026-05-27 — it returns 200 OK with an empty body, so the feed silently
    went dry. The broadest indices NSE still serves here are NIFTY 500 (ranks
    1–500) and NIFTY SMALLCAP 500 (ranks 251–750); their union ≈ the old ~750
    universe. Constituents are merged (deduped by symbol) and stored under one
    label so the dashboard's single-as_of live view works unchanged.

    Shares raw_equity_quotes with LiveEquity; the index_name column keeps the
    feeds distinct (NIFTY 50 vs the broad union)."""

    name = "live_equity_total_market"
    index_name = "NIFTY 500 + SMALLCAP 500"
    fetch_indices = ("NIFTY 500", "NIFTY SMALLCAP 500")


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