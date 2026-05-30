"""Backtest configuration for the BB(20,2) + EMA9 intraday strategy.

Subclasses StrategyConfig — shared fields (strategy, leverage, rr_min, tick,
allow_reentry, extras) live in the base; this class adds intraday-specific
knobs (BB/EMA parameters, gap detection, session timing).

Anything controllable via CLI lives here so the runner can serialize
params_json into backtest_runs.

Time-of-day fields are bar-start IST clock times. With 30-min bars anchored
to 09:15 IST the session grid is:
    09:15 09:45 10:15 10:45 11:15 11:45 12:15 12:45 13:15 13:45 14:15 14:45 15:15

`no_entry_after = "14:30"` means no fills on bars starting >= 14:45.
`force_exit_at  = "15:15"` means open positions exit at the open of that bar.

`BacktestConfig` is kept as the public class name for backward compatibility
with v1 tests and CLI; new strategies should pick a more specific name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..._core.types import StrategyConfig

GapMode = Literal["any", "overnight"]


@dataclass(frozen=True)
class BacktestConfig(StrategyConfig):
    strategy: str = "bb_ema9_30m"
    leverage: float = 5.0                   # 5x MIS multiplier on P&L (override base default)
    rr_min: float = 1.5

    # Indicator parameters
    bb_length: int = 20
    bb_std: float = 2.0
    ema_length: int = 9

    # Signal thresholds
    gap_pct: float = 0.003                  # 0.3% min open-vs-prior-close move
    gap_mode: GapMode = "any"               # "any" = any 30m bar; "overnight" = first bar of session
    uptrend_lookback: int = 5               # bars used for "EMA9 rising"
    min_closes_above_ema: int = 3           # of last `uptrend_lookback`

    # Session timing (IST, HH:MM)
    no_entry_after: str = "14:30"
    force_exit_at: str = "15:15"
