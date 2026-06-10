"""
Open Interest as a daily series, with the buildup read (F&O symbols only).

The OI-spurts feed (`raw_oi_spurts`) snapshots futures OI intraday; this
reduces it to one row per session (the day's last snapshot) and classifies the
day with the OI × price matrix every derivatives desk uses:

    OI ↑  price ↑   →  +1  long buildup       (fresh longs — conviction)
    OI ↑  price ↓   →  −1  short buildup      (fresh shorts — conviction)
    OI ↓  price ↑   →  +2  short covering     (forced exit, weaker fuel)
    OI ↓  price ↓   →  −2  long unwinding     (longs giving up)
    small either way →  0

Price change comes from the symbol's own OHLCV (the orchestrator's input);
OI arrives via ``prepare()``, which loads this symbol's snapshots and keeps
the daily-last per session. Symbols with no F&O data simply produce nothing.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3

import pandas as pd

from ..base import Indicator

_IST_OFFSET = 19800          # raw as_of is UTC epoch; sessions are IST days
_OI_FLAT_PCT = 1.0           # |ΔOI| below this = no signal
_PRICE_FLAT_PCT = 0.1


class OpenInterestEod(Indicator):
    name = "oi"
    table = "indicator_oi"
    pk_cols = ("symbol", "date")
    output_columns = ("oi", "oi_change_pct", "oi_buildup")
    min_history = 2
    pane = "oscillator"
    cadence = "eod"

    def __init__(self) -> None:
        self._oi_by_date: dict[str, tuple[float, float | None]] = {}

    def prepare(self, conn: sqlite3.Connection, symbol: str) -> None:
        self._oi_by_date = {}
        try:
            rows = conn.execute(
                "SELECT as_of, latest_oi, prev_oi FROM raw_oi_spurts "
                "WHERE symbol=? ORDER BY as_of",
                (symbol,),
            ).fetchall()
        except sqlite3.OperationalError:
            return
        for as_of, oi, prev in rows:    # ascending → the day's LAST snapshot wins
            day = _dt.datetime.utcfromtimestamp(int(as_of) + _IST_OFFSET).date().isoformat()
            pct = (100.0 * (oi - prev) / prev) if (prev or 0) > 0 else None
            self._oi_by_date[day] = (float(oi), pct)

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index,
                              columns=list(self.output_columns), dtype="float")
        if not self._oi_by_date:
            return result
        price_pct = ohlcv["close"].pct_change() * 100.0
        for ts in ohlcv.index:
            got = self._oi_by_date.get(str(ts)[:10])
            if got is None:
                continue
            oi, oi_pct = got
            result.at[ts, "oi"] = oi
            if oi_pct is None:
                continue
            result.at[ts, "oi_change_pct"] = oi_pct
            p = price_pct.loc[ts]
            if pd.isna(p) or abs(oi_pct) < _OI_FLAT_PCT or abs(p) < _PRICE_FLAT_PCT:
                result.at[ts, "oi_buildup"] = 0
            elif oi_pct > 0:
                result.at[ts, "oi_buildup"] = 1 if p > 0 else -1
            else:
                result.at[ts, "oi_buildup"] = 2 if p > 0 else -2
        return result
