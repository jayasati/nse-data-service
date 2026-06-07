"""Engine test for the ORB+VWAP benchmark, using synthetic minute bars."""

from __future__ import annotations

from nse_data.backtester.strategies.orb_vwap import (
    OrbVwapConfig, run_backtest_for_symbol,
)

from .conftest import IST, insert_minute_bars


def _cfg(**kw) -> OrbVwapConfig:
    # opening_range_bars=1 -> OR is the first 5-min bar; atr_length=2 so ATR is
    # available early in a short synthetic session.
    return OrbVwapConfig(strategy="orb_vwap", opening_range_bars=1,
                         atr_length=2, atr_mult=1.5, rr_target=1.5, **kw)


def test_orb_breakout_takes_long_and_hits_target(backtest_db):
    # 20 1-min bars (four 5-min bars) on one session from 09:15 IST.
    bars = (
        [(100, 101, 99, 100, 1000)] * 5 +   # bar0 09:15-09:20 -> OR high=101
        [(100, 101, 99, 100, 1000)] * 5 +   # bar1 09:20-09:25
        [(101, 103, 101, 102, 2000)] * 5 +  # bar2 09:25-09:30 breakout (high>101)
        [(102, 108, 102, 107, 2000)] * 5    # bar3 09:30-09:35 entry+target
    )
    insert_minute_bars(backtest_db, "TESTCO", start_ist=IST(2026, 6, 5, 9, 15), bars=bars)

    signals, trades = run_backtest_for_symbol(
        backtest_db, "TESTCO", start_date="2026-06-05", end_date="2026-06-05", cfg=_cfg(),
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "LONG"
    assert t.entry_price == 102.0          # next-bar (bar3) open after the bar2 signal
    assert t.exit_reason == "TARGET"
    assert t.pnl_raw() > 0


def test_orb_no_breakout_no_trade(backtest_db):
    # flat session, never breaks the opening range -> no trade
    bars = [(100, 100.5, 99.5, 100, 1000)] * 25
    insert_minute_bars(backtest_db, "FLATCO", start_ist=IST(2026, 6, 5, 9, 15), bars=bars)
    signals, trades = run_backtest_for_symbol(
        backtest_db, "FLATCO", start_date="2026-06-05", end_date="2026-06-05", cfg=_cfg(),
    )
    assert trades == []
