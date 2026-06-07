"""ORB + VWAP benchmark strategy (Week 11, task 11.5).

Public surface:
    OrbVwapConfig             — strategy config
    run_backtest_for_symbol   — engine entry point used by the runner
"""

from .config import OrbVwapConfig
from .engine import run_backtest_for_symbol

__all__ = ["OrbVwapConfig", "run_backtest_for_symbol"]
