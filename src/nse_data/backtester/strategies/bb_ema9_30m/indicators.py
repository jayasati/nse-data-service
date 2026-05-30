"""In-memory BB(20,2) + EMA(9) computation for the backtester.

This is NOT registered as an Indicator subclass because:
  1. The backtester runs one-shot over months of history per symbol; persisting
     these to a per-bar table just to read them back inside the same run is
     wasted I/O.
  2. There is no 30-min cadence in `indicators/compute.py`; adding one for
     a single strategy's purposes would be over-engineering.
  3. If we later want a live BB+EMA9 scanner, that's the day to register it.

Uses `pandas_ta_classic` to stay consistent with the rest of the indicator
stack (same library = same Wilder/EMA conventions if we ever cross-reference).
"""

from __future__ import annotations

from typing import cast

import pandas as pd
import pandas_ta_classic as ta

from .config import BacktestConfig


def add_bb_ema9(df: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """Append `upper`, `lower`, `ema9`, `x`, `y` columns to `df` and return it.

    `df` must have a `close` column and be indexed by `ts` (UTC epoch seconds)
    ascending. Rows lacking enough history get NaN — caller (signals.py) skips
    them.

    `x = upper - ema9`  (upper-band distance)
    `y = ema9  - lower` (lower-band distance)
    """
    close = cast(pd.Series, df["close"])

    bb = ta.bbands(close, length=cfg.bb_length, std=cfg.bb_std)
    if bb is None or bb.empty:
        # Not enough closes for even one BB row — return df with NaN columns
        # so the engine's iteration can proceed without special-casing.
        out = df.copy()
        out["upper"] = pd.NA
        out["lower"] = pd.NA
        out["ema9"]  = pd.NA
        out["x"]     = pd.NA
        out["y"]     = pd.NA
        return out

    # pandas-ta-classic names its bbands columns BBL/BBM/BBU/BBB/BBP plus the
    # length/std suffix. We only need lower and upper; the middle line (BBM)
    # is by spec replaced by EMA9 for this strategy.
    suffix = f"_{cfg.bb_length}_{cfg.bb_std}"
    lower_col = f"BBL{suffix}"
    upper_col = f"BBU{suffix}"

    ema = ta.ema(close, length=cfg.ema_length)

    out = df.copy()
    out["upper"] = bb[upper_col]
    out["lower"] = bb[lower_col]
    out["ema9"]  = ema if ema is not None else pd.NA
    out["x"] = cast(pd.Series, out["upper"]) - cast(pd.Series, out["ema9"])
    out["y"] = cast(pd.Series, out["ema9"])  - cast(pd.Series, out["lower"])
    return out
