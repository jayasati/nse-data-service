"""Opening Range (ORB) on 5-minute bars — session-anchored, recomputed every minute.

The opening range is the high/low of the session's first 15 minutes (three 5-min bars,
09:15–09:30). It's the day's first battle line: a close above the OR high is an upside breakout,
below the OR low a breakdown, between them the session is still ranging. Like VWAP, the range
resets each session, so the per-session group is keyed on the IST calendar day.

Outputs (per bar, from the 4th bar of each session onward — NaN while the range is still forming):
    orb_high   the opening-range high (fixed for the session once set)
    orb_low    the opening-range low
    orb_break  +1 close above OR high · -1 close below OR low · 0 inside the range
"""
from __future__ import annotations

import pandas as pd

from ...webcore.config import IST_OFFSET
from ..base import Indicator

_SESSION_SECS = 86_400
_OR_BARS = 3   # first 15 minutes = three 5-min bars = the opening range


class OpeningRangeIntraday(Indicator):
    name = "orb_5m"
    table = "indicator_orb_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("orb_high", "orb_low", "orb_break")
    # Needs the session's own 09:15 open in the read window (≤75 five-min bars/session);
    # 78 guarantees the opening range of the latest possible new bar is reachable.
    min_history = 78
    pane = "overlay"
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        if ohlcv.empty:
            for c in self.output_columns:
                result[c] = pd.NA
            return result

        session = (ohlcv.index.to_numpy() + IST_OFFSET) // _SESSION_SECS
        sess = pd.Series(session, index=ohlcv.index)
        oh = pd.Series(pd.NA, index=ohlcv.index, dtype="float64")
        ol = pd.Series(pd.NA, index=ohlcv.index, dtype="float64")
        brk = pd.Series(pd.NA, index=ohlcv.index, dtype="float64")

        for _, idx in sess.groupby(sess).groups.items():
            grp = ohlcv.loc[idx]
            if len(grp) <= _OR_BARS:
                continue                                   # range not complete → leave NaN
            opening = grp.iloc[:_OR_BARS]
            hi = float(opening["high"].max())
            lo = float(opening["low"].min())
            post = grp.index[_OR_BARS:]                    # bars after the opening range
            closes = grp.loc[post, "close"]
            oh.loc[post] = hi
            ol.loc[post] = lo
            brk.loc[post] = closes.gt(hi).astype(float) - closes.lt(lo).astype(float)

        result["orb_high"] = oh
        result["orb_low"] = ol
        result["orb_break"] = brk
        return result
