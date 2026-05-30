"""BB(20,2) + EMA9 30-min intraday gap-hunt strategy.

Public surface:
    BacktestConfig            — strategy config (step 3 renames to BBEma30mConfig)
    run_backtest_for_symbol   — engine entry point used by the runner
"""

from .config import BacktestConfig
from .engine import run_backtest_for_symbol

__all__ = ["BacktestConfig", "run_backtest_for_symbol"]
