"""
NSE all-indices + market breadth.

One endpoint /api/allIndices returns BOTH:
  - 139 index quotes (Nifty, Bank Nifty, sector indices, etc.)
  - Market-wide breadth: advances/declines/unchanged counts

Rather than make two collectors that hit the same URL, we let one collector
write to two tables. raw_indices gets the indices via the normal persist
path; raw_advances_declines gets a single breadth row written directly.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import PersistResult, Request, Row, SnapshotCollector


NSE_BASE = "https://www.nseindia.com"


class Indices(SnapshotCollector):
    name = "indices"
    table = "raw_indices"
    pk_cols = ("index_symbol", "as_of")

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/allIndices",
            referer=f"{NSE_BASE}/market-data/live-market-indices",
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []

        as_of = int(time.time())

        # Stash the breadth counts on the instance for persist() to pick up.
        # This is the deviation from the standard one-collector-one-table
        # pattern; documented in the module docstring.
        self._breadth = {
            "as_of":     as_of,
            "advances":  _i(data.get("advances")),
            "declines":  _i(data.get("declines")),
            "unchanged": _i(data.get("unchanged")),
        }

        items = data.get("data") or []
        rows: list[Row] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sym = (item.get("indexSymbol") or item.get("index") or "").strip()
            if not sym:
                continue
            rows.append({
                "index_symbol": sym,
                "as_of":        as_of,
                "index_name":   item.get("index"),
                "last":         _f(item.get("last")),
                "variation":    _f(item.get("variation")),
                "pct_change":   _f(item.get("percentChange")),
                "open":         _f(item.get("open")),
                "high":         _f(item.get("high")),
                "low":          _f(item.get("low")),
                "prev_close":   _f(item.get("previousClose")),
            })
        return rows

    def persist(self, db, rows: list[Row]) -> PersistResult:
        # Persist the indices via the normal SnapshotCollector path
        result = super().persist(db, rows)

        # Then write the single breadth row directly. We use INSERT OR IGNORE
        # since multiple polls in the same second would collide on PK (as_of).
        if hasattr(self, "_breadth") and self._breadth["advances"] is not None:
            db.execute(
                "INSERT OR IGNORE INTO raw_advances_declines "
                "(as_of, advances, declines, unchanged) VALUES (?, ?, ?, ?)",
                (
                    self._breadth["as_of"],
                    self._breadth["advances"],
                    self._breadth["declines"],
                    self._breadth["unchanged"],
                ),
            )
            db.commit()
        return result


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