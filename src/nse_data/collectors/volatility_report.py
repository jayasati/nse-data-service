"""
CMVOLT — historical volatility per F&O underlying, daily EOD.

URL: https://nsearchives.nseindia.com/archives/nsccl/volt/CMVOLT_<DDMMYYYY>.CSV

NSE serves only annualized HV (column F), not multi-horizon as architecture
§5.8 #56 implied. One row per F&O underlying per trading day.

Use Layer 4 to compute multi-horizon HV from raw_bhavcopy_cm if needed —
this is just NSE's official EWMA-based number.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Any, Mapping, Sequence

from .base import CsvCollector, Request, Row


log = logging.getLogger(__name__)


def _url_for(d: date) -> str:
    return (
        f"https://nsearchives.nseindia.com/archives/nsccl/volt/"
        f"CMVOLT_{d.strftime('%d%m%Y')}.CSV"
    )


class VolatilityReport(CsvCollector):
    name = "volatility_report"
    table = "raw_volatility"
    pk_cols = ("date", "symbol")
    response_type = "text"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        d = (context or {}).get("for_date") or date.today()
        return [Request(
            path_or_url=_url_for(d),
            referer="https://www.nseindia.com/all-reports",
            response_type="text",
            meta={"for_date": d.isoformat()},
        )]

    def normalize(self, data: str, request: Request) -> list[Row]:
        if not isinstance(data, str) or len(data) < 100:
            return []
        reader = csv.DictReader(io.StringIO(data))
        rows: list[Row] = []
        for r in reader:
            # NSE column names have spaces and parentheses — index by position
            # would be brittle. We use the literal names from the CSV.
            symbol = (r.get("Symbol") or "").strip()
            date_str = (r.get("Date") or "").strip()
            if not symbol or not date_str:
                continue
            rows.append({
                "date":                  date_str,
                "symbol":                symbol,
                "underlying_close":      _f(r.get("Underlying Close Price (A)")),
                "underlying_prev_close": _f(r.get("Underlying Previous Day Close Price (B)")),
                "underlying_log_return": _f(r.get("Underlying Log Returns (C) = LN(A/B)")),
                "prev_day_volatility":   _f(r.get("Previous Day Underlying Volatility (D)")),
                "daily_volatility":      _f(r.get("Current Day Underlying Daily Volatility (E) = Sqrt(0.995*D*D + 0.005*C*C)")),
                "annualised_volatility": _f(r.get("Underlying Annualised Volatility (F) = E*Sqrt(365)")),
            })
        return rows

    def run_for_date(self, session, db, d: date):
        return self.run(session, db, context={"for_date": d})


def _f(v):
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None