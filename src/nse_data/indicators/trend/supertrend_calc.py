"""
SuperTrend — the TradingView/industry-standard formula.

Computed directly (rather than via pandas-ta) so we own the warm-up seeding and
the band-lock recursion, but the math matches TradingView's ``ta.supertrend``
to float precision (verified on ADANIGREEN 5-min, 2026-06, and against
pandas-ta-classic's ``SUPERT_10_3.0`` in tests). Two bands, in the code's
trader-oriented names:

    hl2 = (high + low) / 2
    atr = Wilder RMA of true range over `length`             (TV's ta.atr)
    up  = hl2 − mult·atr   (support — sits BELOW price in an uptrend)
    dn  = hl2 + mult·atr   (resistance — sits ABOVE price in a downtrend)

The bands ratchet (the support only rises, the resistance only falls) until
price *closes through* them, at which point that band resets to its raw level:

    final_up[i] = up[i]  if up[i] > final_up[i-1] or close[i-1] < final_up[i-1]
                  else final_up[i-1]                  # reset once close < support
    final_dn[i] = dn[i]  if dn[i] < final_dn[i-1] or close[i-1] > final_dn[i-1]
                  else final_dn[i-1]                  # reset once close > resistance

    # direction flips only when price closes across the active band:
    dir = +1 (up,   line = final_up, sits BELOW price)
          −1 (down, line = final_dn, sits ABOVE price)

Returns the SuperTrend line and direction (+1 long / −1 short). NaN warm-up
rows (the first `length` bars, no ATR yet) are left as NaN so the writer drops
them rather than charting a seeded zero.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series,
    *, length: int = 10, multiplier: float = 3.0,
) -> pd.DataFrame:
    n = len(close)
    out = pd.DataFrame(index=close.index, columns=["supertrend", "supertrend_dir"],
                       dtype="float")
    if n < length + 1:
        return out

    hl2 = (high + low) / 2.0
    prev_close = close.shift(1)
    tr = pd.concat([(high - low),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    # Wilder RMA (== EMA with alpha 1/length, no adjust) — TradingView's ATR.
    atr = tr.ewm(alpha=1.0 / length, adjust=False).mean()

    up = (hl2 - multiplier * atr).to_numpy()
    dn = (hl2 + multiplier * atr).to_numpy()
    cc = close.to_numpy()

    final_up = np.full(n, np.nan)
    final_dn = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.full(n, np.nan)

    # Warm-up: first valid bar is index `length` (ATR settled). Seed there.
    start = length
    final_up[start] = up[start]
    final_dn[start] = dn[start]
    st[start] = dn[start]            # convention: begin in "down" then let price decide
    direction[start] = -1.0
    for i in range(start + 1, n):
        # Support ratchets up; resets only once the prior close breaks BELOW it.
        final_up[i] = (up[i] if (up[i] > final_up[i - 1] or cc[i - 1] < final_up[i - 1])
                       else final_up[i - 1])
        # Resistance ratchets down; resets only once the prior close breaks ABOVE it.
        final_dn[i] = (dn[i] if (dn[i] < final_dn[i - 1] or cc[i - 1] > final_dn[i - 1])
                       else final_dn[i - 1])
        if st[i - 1] == final_dn[i - 1]:          # was in downtrend
            direction[i] = 1.0 if cc[i] > final_dn[i] else -1.0
        else:                                      # was in uptrend
            direction[i] = -1.0 if cc[i] < final_up[i] else 1.0
        st[i] = final_up[i] if direction[i] == 1.0 else final_dn[i]

    out["supertrend"] = st
    out["supertrend_dir"] = direction
    return out
