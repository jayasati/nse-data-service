"""Config for the 52-week-high breakout backtest strategy.

The daily-bar backtest counterpart of the live `breakout_52wh` signal
(FEATURE_CHECKLIST 4.6). The live rule needs the intraday 52w-high feed + 5-min
volume; here we reconstruct both from daily bhavcopy, which we have years of:

    new 52w high   today's high > the max high over the prior `lookback_52w` bars
    volume filter  today's volume ≥ `vol_ratio_min` × the trailing `vol_lookback` avg
    bracket        ATR-based, matching the live SL/T1 (FEATURE_CHECKLIST 5.4):
                   SL = entry − atr_mult×ATR, T1 = entry + atr_mult×ATR (1R)

Because daily bars can't model the live "flat by 15:20" intraday exit, this runs
as a short-horizon swing: hold until SL/T1 hits, capped at `max_hold_days`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..._core.types import StrategyConfig


@dataclass(frozen=True)
class Breakout52whConfig(StrategyConfig):
    strategy: str = "breakout_52wh"
    leverage: float = 1.0
    rr_min: float = 0.0                  # 1.5×ATR each side -> rr is always 1.0; don't pre-filter

    # 52-week-high detection
    lookback_52w: int = 252             # trading days in ~1 year
    min_history: int = 60               # need at least this many prior bars to call a "high"

    # Volume confirmation (FEATURE_CHECKLIST 4.6: volume_ratio >= 1.5)
    vol_lookback: int = 20
    vol_ratio_min: float = 1.5

    # ATR bracket (FEATURE_CHECKLIST 5.4)
    atr_length: int = 14
    atr_mult: float = 1.5

    # Trade management
    max_hold_days: int = 5              # exit at close after N bars if neither level hit
    gap_fill: str = "open"             # "open" | "sl" — fill price when a bar gaps through SL
    max_gap_pct: float = 0.5           # skip corporate-action-sized entry gaps
