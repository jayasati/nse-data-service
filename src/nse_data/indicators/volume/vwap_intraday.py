"""
Session-anchored VWAP on 5-minute bars — recomputed every minute during the
session.

VWAP (volume-weighted average price) is the running ratio

    vwap = cumsum(typical_price × volume) / cumsum(volume)

where typical_price = (high + low + close) / 3, accumulated *from the session
open*. Unlike the rolling RSI/MACD intraday indicators, the cumulative sums
**reset at 09:15 each day** — VWAP answers "average price paid so far today",
so blending it across sessions would be meaningless.

`ts` is UTC epoch seconds at the 5-min bar start. The in-progress bar gets
overwritten ~5 times before it closes (INSERT OR REPLACE on the next pass),
which is what gives the live VWAP its <1-minute latency.
"""

from __future__ import annotations

import pandas as pd

from ...webcore.config import IST_OFFSET
from ..base import Indicator

_SESSION_SECS = 86_400


class VwapIntraday(Indicator):
    name = "vwap_5m"
    table = "indicator_vwap_5m"
    pk_cols = ("symbol", "ts")            # epoch-second key, not date string
    output_columns = ("vwap",)
    # VWAP is session-cumulative, not rolling: a correct value for any new bar
    # needs the read window to reach back to *that bar's* 09:15 open. A full
    # NSE session is 09:15–15:30 = 75 five-min bars, so 78 bars of lookback
    # guarantees the open is in range for the latest possible new bar. Earlier
    # sessions in the window are whole; the incremental writer drops the
    # partial warm-up session (rows ≤ watermark), so the over-pull is harmless.
    min_history = 78
    pane = "overlay"                      # rides the price axis, like an EMA
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        if ohlcv.empty:
            result["vwap"] = pd.NA
            return result

        typical = (ohlcv["high"] + ohlcv["low"] + ohlcv["close"]) / 3.0
        volume = ohlcv["volume"].fillna(0.0)

        # Session key = IST calendar day. The index is UTC epoch seconds; adding
        # the IST offset before the day-floor puts the boundary at the IST
        # midnight preceding the 09:15 open, so each trading day is its own
        # group and the cumulative sums restart at the session open.
        session = (ohlcv.index.to_numpy() + IST_OFFSET) // _SESSION_SECS

        cum_pv = (typical * volume).groupby(session).cumsum()
        cum_vol = volume.groupby(session).cumsum()

        # Before any volume has printed in a session (cum_vol == 0) VWAP is
        # undefined → NaN. The writer skips all-NaN rows rather than persist a
        # divide-by-zero.
        result["vwap"] = cum_pv / cum_vol.where(cum_vol > 0)
        return result
