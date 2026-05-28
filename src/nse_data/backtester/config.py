"""Backtest configuration for the BB(20,2) + EMA9 intraday strategy.

One @dataclass holds every knob the strategy and engine need. Defaults match
the user's spec (Strategy 1.1, Aseem Singhal). Anything controllable via CLI
lives here so the runner can serialize params_json into backtest_runs.

Time-of-day fields are bar-start IST clock times. With 30-min bars anchored
to 09:15 IST the session grid is:
    09:15 09:45 10:15 10:45 11:15 11:45 12:15 12:45 13:15 13:45 14:15 14:45 15:15

`no_entry_after = "14:30"` means no fills on bars starting >= 14:45.
`force_exit_at  = "15:15"` means open positions exit at the open of that bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

GapMode = Literal["any", "overnight"]
Strategy = Literal["bb_ema9_30m"]


@dataclass(frozen=True)
class BacktestConfig:
    strategy: Strategy = "bb_ema9_30m"

    # Indicator parameters
    bb_length: int = 20
    bb_std: float = 2.0
    ema_length: int = 9

    # Signal thresholds
    gap_pct: float = 0.003                  # 0.3% min open-vs-prior-close move
    gap_mode: GapMode = "any"               # "any" = any 30m bar; "overnight" = first bar of session
    uptrend_lookback: int = 5               # bars used for "EMA9 rising"
    min_closes_above_ema: int = 3           # of last `uptrend_lookback`
    rr_min: float = 1.5                     # skip setups below this R:R

    # Trade management
    tick: float = 0.05                      # NSE equity tick size
    leverage: float = 5.0                   # 5x MIS multiplier on P&L
    allow_reentry: bool = False             # one trade per symbol per session

    # Session timing (IST, HH:MM)
    no_entry_after: str = "14:30"
    force_exit_at: str = "15:15"

    # Extra metadata for params_json (informational only)
    extras: dict = field(default_factory=dict)
