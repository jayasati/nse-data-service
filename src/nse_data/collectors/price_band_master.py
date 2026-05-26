"""
Daily price-band master — every equity's applicable price band for the session.

Source: https://nsearchives.nseindia.com/content/equities/sec_list.csv
A fixed-URL daily CSV (Symbol, Series, Security Name, Band, Remarks). Pulled
before market open so band restrictions are known ahead of trading.

Distinct from collectors/price_band.py: that one snapshots *intraday circuit
hits* (which stocks hit upper/lower band right now); this one is the daily
*assignment* of bands (2/5/10/20%) per security.

It also carries the T2T / restricted-segment split via Series (EQ = rolling;
BE/BZ/ST = trade-for-trade), so "T2T segment" is a Series filter over this
table. Remarks carries surveillance notes (e.g. 'GSM STAGE - II').

ReferenceCollector (diff_upsert): the current file IS the truth. Keyed on
(symbol, series) since a symbol may list under two series. No capture
timestamp, so a band tightening shows as a genuine 'updated' diff.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Mapping, Sequence

from .base import ReferenceCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"
SURV_REFERER = f"{NSE_BASE}/market-data/price-bands-surveillance-actions"
SEC_LIST_URL = "https://nsearchives.nseindia.com/content/equities/sec_list.csv"


class PriceBandMaster(ReferenceCollector):
    name = "price_bands"
    table = "raw_price_bands"
    key_cols = ("symbol", "series")
    replace_strategy = "diff"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url=SEC_LIST_URL,
            referer=SURV_REFERER,
            response_type="text",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, str) or not data.strip():
            return []
        rows: list[Row] = []
        for item in csv.DictReader(io.StringIO(data)):
            symbol = (item.get("Symbol") or "").strip()
            series = (item.get("Series") or "").strip()
            if not symbol or not series:
                continue
            rows.append({
                "symbol":        symbol,
                "series":        series,
                "security_name": (item.get("Security Name") or "").strip() or None,
                "band":          _band(item.get("Band")),
                "remarks":       _dash(item.get("Remarks")),
            })
        return rows


def _band(v):
    """'2'/'5'/'10'/'20' -> int; 'No Band' / blank -> None."""
    if v is None:
        return None
    v = str(v).strip()
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _dash(v):
    """NSE '-' placeholder -> None."""
    if v is None:
        return None
    v = str(v).strip()
    return None if v in ("", "-") else v
