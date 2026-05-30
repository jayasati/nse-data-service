"""Daily-engine invariants — multi-day hold, %R extreme exit, EOD_HISTORY, gap-fill."""

from __future__ import annotations

import pandas as pd
import pytest

from nse_data.backtester.strategies.macd_willr_daily.config import MacdWillrDailyConfig
from nse_data.backtester.strategies.macd_willr_daily.engine import run_backtest_on_bars


def _df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).set_index("date")
    return df


def _bar(date: str, o: float, h: float, low: float, c: float,
         willr: float = -50.0, macd: float = 0.1, sig: float = 0.05) -> dict:
    return {
        "date": date, "open": o, "high": h, "low": low, "close": c,
        "volume": 1000, "willr": willr, "macd": macd, "macd_signal": sig,
        "macd_hist": macd - sig,
    }


def _setup_long_then_filler(target_hit_on_day: int) -> list[dict]:
    """Setup at day 5; entry at day 6 open; target hit at given day."""
    rows = [
        _bar("2026-01-01", 100, 101, 99, 100, willr=-50, macd=0.0, sig=0.0),
        _bar("2026-01-02", 100, 101, 99, 100, willr=-60, macd=0.0, sig=0.0),
        _bar("2026-01-03", 100, 101, 99, 100, willr=-70, macd=0.0, sig=0.0),
        _bar("2026-01-04", 100, 101, 99, 100, willr=-82, macd=0.0, sig=0.0),
        # Setup bar: willr was -85, now hooks back up to -75. MACD positive.
        _bar("2026-01-05", 100, 101, 95, 100, willr=-75, macd=0.5, sig=0.3),
        # Entry bar: open at 101
        _bar("2026-01-06", 101, 103, 100, 102, willr=-60, macd=0.5, sig=0.3),
    ]
    # Filler bars until target_hit_on_day. Need to compute target = 101 + 2 * (101 - sl)
    # sl = min(low last 10 bars = [99 99 99 99 95 100]) - 0.05 = 95 - 0.05 = 94.95
    # target = 101 + 2 * (101 - 94.95) = 101 + 12.1 = 113.1
    for d in range(7, target_hit_on_day):
        rows.append(_bar(f"2026-01-{d:02d}", 102, 103, 101.5, 102.5,
                         willr=-50, macd=0.5, sig=0.3))
    # Target hit bar: high reaches 114
    day = target_hit_on_day
    rows.append(_bar(f"2026-01-{day:02d}", 103, 114, 102, 113,
                     willr=-30, macd=0.5, sig=0.3))
    return rows


def test_engine_multi_day_hold_to_target():
    cfg = MacdWillrDailyConfig(willr_length=2, swing_lookback=10,
                                rr_target=2.0, use_divergence=False, rr_min=0.0)
    rows = _setup_long_then_filler(target_hit_on_day=10)
    df = _df(rows)

    _, trades = run_backtest_on_bars(df, cfg)

    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "LONG"
    assert t.exit_reason == "TARGET"
    assert t.exit_price == pytest.approx(t.target)
    assert "basic" in (t.signal_tags or "")


def test_engine_eod_history_when_position_open_at_end():
    cfg = MacdWillrDailyConfig(willr_length=2, swing_lookback=10,
                                rr_target=2.0, use_divergence=False, rr_min=0.0)
    # Setup + entry but NO target/SL hit; bars run out while open
    rows = [
        _bar("2026-01-01", 100, 101, 99, 100, willr=-50, macd=0.0, sig=0.0),
        _bar("2026-01-02", 100, 101, 99, 100, willr=-60, macd=0.0, sig=0.0),
        _bar("2026-01-03", 100, 101, 99, 100, willr=-70, macd=0.0, sig=0.0),
        _bar("2026-01-04", 100, 101, 99, 100, willr=-82, macd=0.0, sig=0.0),
        _bar("2026-01-05", 100, 101, 95, 100, willr=-75, macd=0.5, sig=0.3),
        _bar("2026-01-06", 101, 103, 100, 102, willr=-60, macd=0.5, sig=0.3),
        # Drift sideways with no extremes
        _bar("2026-01-07", 102, 103, 101, 102, willr=-55, macd=0.5, sig=0.3),
        _bar("2026-01-08", 102, 103, 101, 102.5, willr=-50, macd=0.5, sig=0.3),
    ]
    df = _df(rows)

    _, trades = run_backtest_on_bars(df, cfg)

    assert len(trades) == 1
    assert trades[0].exit_reason == "EOD_HISTORY"
    assert trades[0].exit_price == 102.5    # last close


def test_engine_willr_extreme_exit_for_long():
    """Long trade exits via WILLR_EXTREME on the first bar where willr crosses
    the overbought threshold (at close). The engine may detect further setups
    later in the test history; we only assert the FIRST trade's exit shape."""
    cfg = MacdWillrDailyConfig(willr_length=2, swing_lookback=10, rr_target=10.0,
                                use_divergence=False, rr_min=0.0,
                                willr_overbought=-20.0)
    rows = [
        _bar("2026-01-01", 100, 101, 99, 100, willr=-50, macd=0.0, sig=0.0),
        _bar("2026-01-02", 100, 101, 99, 100, willr=-82, macd=0.0, sig=0.0),
        _bar("2026-01-03", 100, 101, 99, 100, willr=-75, macd=0.5, sig=0.3),
        _bar("2026-01-04", 101, 103, 100, 102, willr=-60, macd=0.5, sig=0.3),
        # willr crosses into overbought zone — exit at close
        _bar("2026-01-05", 102, 103, 101, 102.5, willr=-15, macd=0.5, sig=0.3),
        _bar("2026-01-06", 103, 104, 102, 103, willr=-15, macd=0.5, sig=0.3),
    ]
    df = _df(rows)

    _, trades = run_backtest_on_bars(df, cfg)

    assert len(trades) >= 1
    assert trades[0].exit_reason == "WILLR_EXTREME"
    assert trades[0].exit_price == 102.5   # close of bar where willr crossed


def test_engine_gap_through_sl_with_open_fill():
    """LONG entered at open of bar 4 = 101. SL ≈ 94.95.
    Bar 5 opens at 90 — gaps THROUGH the SL. With gap_fill='open' (default),
    exit at 90, not at SL."""
    cfg = MacdWillrDailyConfig(willr_length=2, swing_lookback=10, rr_target=2.0,
                                use_divergence=False, rr_min=0.0,
                                gap_fill="open")
    rows = [
        _bar("2026-01-01", 100, 101, 99, 100, willr=-50, macd=0.0, sig=0.0),
        _bar("2026-01-02", 100, 101, 99, 100, willr=-82, macd=0.0, sig=0.0),
        _bar("2026-01-03", 100, 101, 99, 100, willr=-75, macd=0.5, sig=0.3),
        _bar("2026-01-04", 101, 103, 100, 102, willr=-60, macd=0.5, sig=0.3),
        # Bar 4 opens BELOW the SL (~94.95)
        _bar("2026-01-05", 90, 95, 85, 92, willr=-90, macd=0.0, sig=0.5),
    ]
    df = _df(rows)

    _, trades = run_backtest_on_bars(df, cfg)

    assert len(trades) == 1
    assert trades[0].exit_reason == "STOP"
    assert trades[0].exit_price == 90.0    # opened the bar at the gap-down


def test_engine_gap_through_sl_with_sl_fill():
    """Same setup; with gap_fill='sl', exit price = SL exactly."""
    cfg = MacdWillrDailyConfig(willr_length=2, swing_lookback=10, rr_target=2.0,
                                use_divergence=False, rr_min=0.0,
                                gap_fill="sl")
    rows = [
        _bar("2026-01-01", 100, 101, 99, 100, willr=-50, macd=0.0, sig=0.0),
        _bar("2026-01-02", 100, 101, 99, 100, willr=-82, macd=0.0, sig=0.0),
        _bar("2026-01-03", 100, 101, 99, 100, willr=-75, macd=0.5, sig=0.3),
        _bar("2026-01-04", 101, 103, 100, 102, willr=-60, macd=0.5, sig=0.3),
        _bar("2026-01-05", 90, 95, 85, 92, willr=-90, macd=0.0, sig=0.5),
    ]
    df = _df(rows)

    _, trades = run_backtest_on_bars(df, cfg)

    assert len(trades) == 1
    assert trades[0].exit_reason == "STOP"
    # SL = min(low last 10) - tick. Lows up to setup bar (index 2): 99,99,99 -> sl=99-0.05=98.95
    assert trades[0].exit_price == pytest.approx(98.95)


def test_engine_skips_corporate_action_sized_gap():
    """A 60% gap-up between the setup bar's close and the next bar's open
    (looks like a bonus issue) should NOT produce an entry at that gapped open.
    To isolate the test, we end the bars right after the gap so no later
    setup can muddy the assertion."""
    cfg = MacdWillrDailyConfig(willr_length=2, swing_lookback=10, rr_target=2.0,
                                use_divergence=False, rr_min=0.0,
                                max_gap_pct=0.5)
    rows = [
        _bar("2026-01-01", 100, 101, 99, 100, willr=-50, macd=0.0, sig=0.0),
        _bar("2026-01-02", 100, 101, 99, 100, willr=-82, macd=0.0, sig=0.0),
        _bar("2026-01-03", 100, 101, 99, 100, willr=-75, macd=0.5, sig=0.3),
        # Bar 3 opens at 160 (60% gap from prev close 100) → reject fill
        _bar("2026-01-04", 160, 165, 158, 162, willr=-30, macd=0.5, sig=0.3),
    ]
    df = _df(rows)

    signals, trades = run_backtest_on_bars(df, cfg)
    # Signal at bar 2 detected; no trade because gap filter killed the fill
    assert len(signals) >= 1
    assert len(trades) == 0
