"""52-week-high breakout daily engine (strategies/breakout_52wh)."""

from __future__ import annotations

import pandas as pd

from nse_data.backtester.strategies.breakout_52wh.config import Breakout52whConfig
from nse_data.backtester.strategies.breakout_52wh.engine import run_backtest_on_bars

# Small windows so a hand-crafted ~15-bar frame satisfies warm-up.
CFG = Breakout52whConfig(
    lookback_52w=10, min_history=3, vol_lookback=3, atr_length=3,
    atr_mult=1.5, max_hold_days=3, vol_ratio_min=1.5,
)


def _bars(rows: list[tuple]) -> pd.DataFrame:
    """rows: (o, h, l, c, volume); dates are sequential trading days."""
    dates = [f"2026-01-{i+1:02d}" for i in range(len(rows))]
    df = pd.DataFrame(rows, columns=pd.Index(["open", "high", "low", "close", "volume"]))
    df.index = pd.Index(dates)
    return df


# 12 flat warm-up bars: high 101, low 99 -> prior 52w high ~101, ATR ~2.
_WARMUP = [(100, 101, 99, 100, 1000)] * 12


def test_breakout_with_volume_hits_target():
    bars = _bars(_WARMUP + [
        (101, 106, 101, 105, 5000),    # breakout: new high 106 > 101, vol 5x
        (105, 106, 104, 105, 1000),    # entry bar (fill at open 105)
        (105, 200, 105, 180, 1000),    # rally -> target hit
    ])
    signals, trades = run_backtest_on_bars(bars, CFG)
    assert len(signals) >= 1
    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "LONG"
    assert t.exit_reason == "TARGET"
    assert t.pnl_raw() > 0


def test_no_breakout_no_trade():
    # Never exceeds the prior high (101) -> no setup.
    bars = _bars(_WARMUP + [
        (100, 101, 99, 100, 5000),
        (100, 101, 99, 100, 5000),
    ])
    signals, trades = run_backtest_on_bars(bars, CFG)
    assert signals == [] and trades == []


def test_breakout_without_volume_skipped():
    bars = _bars(_WARMUP + [
        (101, 106, 101, 105, 1000),    # new high but volume == average (ratio 1.0)
        (105, 106, 104, 105, 1000),
    ])
    signals, trades = run_backtest_on_bars(bars, CFG)
    assert signals == [] and trades == []


def test_stop_loss_hit():
    bars = _bars(_WARMUP + [
        (101, 106, 101, 105, 5000),    # breakout
        (105, 106, 104, 105, 1000),    # entry at 105
        (105, 105, 50, 60, 1000),      # crash -> SL breached
    ])
    _, trades = run_backtest_on_bars(bars, CFG)
    assert len(trades) == 1
    assert trades[0].exit_reason == "STOP"
    assert trades[0].pnl_raw() < 0


def test_max_hold_timeout():
    # Entry then flat bars within the bracket -> exit at close on MAX_HOLD.
    flat = (105, 105, 105, 105, 1000)
    bars = _bars(_WARMUP + [
        (101, 106, 101, 105, 5000),    # breakout
        flat, flat, flat, flat, flat,  # never hits SL/T1
    ])
    _, trades = run_backtest_on_bars(bars, CFG)
    assert len(trades) == 1
    assert trades[0].exit_reason == "MAX_HOLD"


def test_empty_bars():
    assert run_backtest_on_bars(pd.DataFrame(), CFG) == ([], [])
