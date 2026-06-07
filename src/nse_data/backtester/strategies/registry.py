"""Strategy registry — single greppable place that maps a strategy name to
its engine entry-point and config class.

Adding strategy #3 = one new line in STRATEGIES. The runner looks up
`STRATEGIES[cfg.strategy].engine_fn` and dispatches per-symbol.

We import each strategy module explicitly here so the wiring is greppable
(not via side-effect imports in some __init__.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .._core.types import Signal, StrategyConfig, Trade
from . import bb_ema9_30m, breakout_52wh, macd_willr_daily

EngineFn = Callable[..., tuple[list[Signal], list[Trade]]]


@dataclass(frozen=True)
class StrategySpec:
    engine_fn: EngineFn
    config_cls: type[StrategyConfig]


STRATEGIES: dict[str, StrategySpec] = {
    "bb_ema9_30m": StrategySpec(
        engine_fn=bb_ema9_30m.run_backtest_for_symbol,
        config_cls=bb_ema9_30m.BacktestConfig,
    ),
    "macd_willr_daily": StrategySpec(
        engine_fn=macd_willr_daily.run_backtest_for_symbol,
        config_cls=macd_willr_daily.MacdWillrDailyConfig,
    ),
    "breakout_52wh": StrategySpec(
        engine_fn=breakout_52wh.run_backtest_for_symbol,
        config_cls=breakout_52wh.Breakout52whConfig,
    ),
}


def resolve(strategy: str) -> StrategySpec:
    """Look up a strategy spec by name. Raises KeyError on unknown."""
    if strategy not in STRATEGIES:
        raise KeyError(
            f"unknown strategy {strategy!r}; "
            f"registered: {sorted(STRATEGIES)}"
        )
    return STRATEGIES[strategy]
