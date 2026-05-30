"""Williams %R (14) + MACD (12,26,9) on daily bars — multi-day swing strategy.

Public surface:
    MacdWillrDailyConfig      — strategy config
    run_backtest_for_symbol   — engine entry point used by the runner

The engine import is gated on the module existing so tests can import the
config + bars + indicators while we're still building the engine in step 6.
"""

from .config import MacdWillrDailyConfig

try:
    from .engine import run_backtest_for_symbol  # type: ignore
except ImportError:
    run_backtest_for_symbol = None  # type: ignore

__all__ = ["MacdWillrDailyConfig", "run_backtest_for_symbol"]
