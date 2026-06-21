"""
Market structure (price action) — confirmed swing points and the HH-HL/LH-LL
state.

A swing high is a bar whose high exceeds the `_K` bars on each side (fractal
confirmation — so a swing is only known `_K` bars after it forms; values are
stamped from the confirmation bar onward, never look-ahead). Structure state
compares the last two confirmed swings of each kind:

    +1  uptrend    — higher high AND higher low
    -1  downtrend  — lower high AND lower low
     0  mixed      — anything else (range / transition)

`swing_high` / `swing_low` carry the latest confirmed levels — the structural
break lines a price-action trader watches (close above the last swing high in
a downtrend = character change).
"""

from __future__ import annotations

import pandas as pd

from ..base import Indicator

_K = 3   # bars each side for fractal confirmation


def _structure_frame(ohlcv: pd.DataFrame, k: int = _K) -> pd.DataFrame:
    """Swing highs/lows + HH-HL/LH-LL structure. `k` = fractal bars each side
    (configurable so multi-timeframe strategies can pass their own lookback)."""
    high, low = ohlcv["high"], ohlcv["low"]
    n = len(ohlcv)
    win = 2 * k + 1
    is_sh = high.rolling(win, center=True).max() == high
    is_sl = low.rolling(win, center=True).min() == low

    swing_high = [None] * n
    swing_low = [None] * n
    structure = [None] * n
    prev_sh = last_sh = prev_sl = last_sl = None
    for i in range(n):
        # the swing at i−k is confirmed once bar i exists
        j = i - k
        if j >= k:
            if is_sh.iloc[j]:
                prev_sh, last_sh = last_sh, float(high.iloc[j])
            if is_sl.iloc[j]:
                prev_sl, last_sl = last_sl, float(low.iloc[j])
        swing_high[i], swing_low[i] = last_sh, last_sl
        if None not in (prev_sh, last_sh, prev_sl, last_sl):
            hh, hl = last_sh > prev_sh, last_sl > prev_sl
            lh, ll = last_sh < prev_sh, last_sl < prev_sl
            structure[i] = 1 if (hh and hl) else -1 if (lh and ll) else 0

    out = pd.DataFrame(index=ohlcv.index)
    out["swing_high"] = pd.array(swing_high, dtype="float")
    out["swing_low"] = pd.array(swing_low, dtype="float")
    out["structure"] = pd.array(structure, dtype="float")
    return out


class MarketStructure(Indicator):
    name = "structure"
    table = "indicator_structure"
    pk_cols = ("symbol", "date")
    output_columns = ("swing_high", "swing_low", "structure")
    # The HH-HL/LH-LL state is a flag, not a price — API-only (bot reads it).
    column_panes = {"structure": "hidden"}
    # enough history that several swings exist before the first printed state
    min_history = _K * 2 * 10
    pane = "overlay"
    cadence = "eod"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        return _structure_frame(ohlcv)


class MarketStructureIntraday(MarketStructure):
    """The same swing logic on 5-minute bars (session structure)."""

    name = "structure_5m"
    table = "indicator_structure_5m"
    pk_cols = ("symbol", "ts")
    cadence = "intraday"
