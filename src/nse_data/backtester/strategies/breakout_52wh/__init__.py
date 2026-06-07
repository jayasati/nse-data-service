"""52-week-high breakout on daily bars — the backtest counterpart of the live
`breakout_52wh` signal (FEATURE_CHECKLIST 4.6).

Public surface:
    Breakout52whConfig        — strategy config
    run_backtest_for_symbol   — engine entry point used by the runner
"""

from .config import Breakout52whConfig
from .engine import run_backtest_for_symbol

__all__ = ["Breakout52whConfig", "run_backtest_for_symbol"]
