"""Step 5 — Fair Value Gap (3-candle imbalance model).

A FVG is the unfilled gap left by a fast 3-candle move (candle 2 runs so hard that candle 1 and
candle 3 don't overlap). The gap stamps on the THIRD candle (the one that completes it):

  Bullish FVG — candle1.high < candle3.low   → gap = [candle1.high, candle3.low]
  Bearish FVG — candle1.low  > candle3.high  → gap = [candle3.high, candle1.low]

Entry (Step 6) is a limit revisit into this zone. Pure + vectorised; look-ahead-safe (the gap
is known the moment candle 3 closes). `gap_mid` is the 50% fill level (a common entry anchor).
"""
from __future__ import annotations

import pandas as pd


def detect_fvgs(df: pd.DataFrame) -> pd.DataFrame:
    """Per-bar FVG tags (stamped on candle 3). Columns: fvg_dir ('bull'|'bear'|None),
    gap_low, gap_high, gap_mid."""
    n = len(df)
    out = pd.DataFrame(index=df.index)
    out["fvg_dir"] = pd.Series([None] * n, index=df.index, dtype="object")
    for col in ("gap_low", "gap_high", "gap_mid"):
        out[col] = pd.array([None] * n, dtype="float")
    if n < 3:
        return out

    high, low = df["high"], df["low"]
    di = out.columns.get_loc("fvg_dir")
    lo_i, hi_i, mid_i = (out.columns.get_loc(c) for c in ("gap_low", "gap_high", "gap_mid"))
    for i in range(2, n):
        c1_high, c1_low = high.iloc[i - 2], low.iloc[i - 2]
        c3_high, c3_low = high.iloc[i], low.iloc[i]
        if c1_high < c3_low:                       # bullish imbalance
            glo, ghi, d = float(c1_high), float(c3_low), "bull"
        elif c1_low > c3_high:                     # bearish imbalance
            glo, ghi, d = float(c3_high), float(c1_low), "bear"
        else:
            continue
        out.iloc[i, di] = d
        out.iloc[i, lo_i], out.iloc[i, hi_i], out.iloc[i, mid_i] = glo, ghi, (glo + ghi) / 2
    return out
