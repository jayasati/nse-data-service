"""Backtest configuration for the Williams %R + MACD daily swing strategy.

Subclasses StrategyConfig. Defaults match the user's spec:
    Williams %R length 14, oversold -80, overbought -20
    MACD 12/26/9
    SL = recent swing low, target = 2× risk (1:2 R:R)
    Exit on first-to-trigger of {SL, target, %R extreme}
    1× leverage (CNC delivery, not MIS)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..._core.types import StrategyConfig

GapFill = Literal["open", "sl"]


@dataclass(frozen=True)
class MacdWillrDailyConfig(StrategyConfig):
    strategy: str = "macd_willr_daily"
    leverage: float = 1.0                   # CNC delivery, no MIS multiplier
    rr_min: float = 0.0                     # don't pre-filter; rr_target controls reward

    # Williams %R
    willr_length: int = 14
    willr_oversold: float = -80.0           # long entry zone (willr <)
    willr_overbought: float = -20.0         # short entry / long exit zone
    willr_hook_lookback: int = 3            # bars after the dip in which the hook qualifies

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    require_fresh_macd_cross: bool = False  # default uses "hist > 0 AND macd > signal"

    # Trade management
    swing_lookback: int = 10                # bars used to find SL anchor
    rr_target: float = 2.0                  # target = entry + rr_target × (entry - sl)
    gap_fill: GapFill = "open"              # "open" | "sl" — fill price when bar gaps through SL

    # Divergence
    use_divergence: bool = True
    pivot_window: int = 5                   # ± bars to confirm a swing pivot
    divergence_lookback: int = 60           # max bars between the two pivots

    # Sanity filter for corporate actions on raw bhavcopy
    max_gap_pct: float = 0.5                # skip bars where (prev_close - open)/prev_close > this
