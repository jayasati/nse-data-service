"""Config for the ORB + VWAP benchmark strategy (Week 11, task 11.5)."""

from __future__ import annotations

from dataclasses import dataclass

from ..._core.types import StrategyConfig


@dataclass(frozen=True)
class OrbVwapConfig(StrategyConfig):
    strategy: str = "orb_vwap"
    # Opening range = first N 5-min bars (3 = 09:15–09:30).
    opening_range_bars: int = 3
    # ATR over the last N 5-min bars, used to size the stop.
    atr_length: int = 6
    atr_mult: float = 1.5      # SL = entry − atr_mult × ATR
    rr_target: float = 1.5     # target = entry + rr_target × risk
