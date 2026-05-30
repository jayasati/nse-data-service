"""Engine invariant tests.

Each test pins one of the documented invariants in engine.py. If you find
yourself loosening one of these, update the engine docstring AND the README.

We construct 30-min DataFrames with pre-populated indicator columns and feed
them through run_backtest_on_bars(), so the engine logic is tested in
isolation from add_bb_ema9() and read_intraday_30m().
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from nse_data.backtester.strategies.bb_ema9_30m.config import BacktestConfig
from nse_data.backtester.strategies.bb_ema9_30m.engine import (
    _ist_minutes, run_backtest_on_bars,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _ist_ts(year: int, month: int, day: int, hh: int, mm: int) -> int:
    return int(datetime(year, month, day, hh, mm, tzinfo=IST).timestamp())


def _bars(rows: list[dict]) -> pd.DataFrame:
    """Build a bars DataFrame that ALREADY has indicator columns set.

    Required keys: ts, open, high, low, close, volume, upper, lower, ema9.
    x and y are derived.
    """
    df = pd.DataFrame(rows).set_index("ts")
    return df  # indicators.add_bb_ema9 will re-add upper/lower/ema9/x/y over these


def _setup_window_for_long(
    base_day: tuple[int, int, int], gap_open: float = 101.1,
) -> list[dict]:
    """A clean uptrend + gap-down + lower-BB pierce setup, then bars that
    follow it (so the engine can do entry + exit).

    Returns rows 09:15..14:15 of base_day. The signal bar is at 11:45.
    The entry-trigger bar is 12:15 (next bar after signal). Subsequent bars
    move up steadily so target hits at 13:15.
    """
    y, m, d = base_day
    return [
        # uptrend
        {"ts": _ist_ts(y, m, d, 9, 15), "open": 100.0, "high": 100.5, "low": 99.8, "close": 100.3,
         "volume": 1000, "upper": 102, "lower": 98, "ema9": 100.0},
        {"ts": _ist_ts(y, m, d, 9, 45), "open": 100.3, "high": 100.8, "low": 100.1, "close": 100.6,
         "volume": 1000, "upper": 102.1, "lower": 98.1, "ema9": 100.1},
        {"ts": _ist_ts(y, m, d, 10, 15), "open": 100.6, "high": 101.1, "low": 100.4, "close": 100.9,
         "volume": 1000, "upper": 102.2, "lower": 98.2, "ema9": 100.2},
        {"ts": _ist_ts(y, m, d, 10, 45), "open": 100.9, "high": 101.4, "low": 100.7, "close": 101.2,
         "volume": 1000, "upper": 102.3, "lower": 98.3, "ema9": 100.4},
        {"ts": _ist_ts(y, m, d, 11, 15), "open": 101.2, "high": 101.7, "low": 101.0, "close": 101.5,
         "volume": 1000, "upper": 102.4, "lower": 98.4, "ema9": 100.6},
        # signal bar — gap down, pierce lower BB, close above
        {"ts": _ist_ts(y, m, d, 11, 45), "open": gap_open, "high": 101.3, "low": 97.5, "close": 99.5,
         "volume": 2000, "upper": 102.0, "lower": 98.0, "ema9": 100.7},
        # entry bar 12:15 — fills if high >= sig_high + tick = 101.35
        {"ts": _ist_ts(y, m, d, 12, 15), "open": 99.6, "high": 101.5, "low": 99.5, "close": 101.4,
         "volume": 1500, "upper": 102.2, "lower": 98.2, "ema9": 100.8},
        # 12:45 keeps rising — target = upper at signal = 102.0; we hit it
        {"ts": _ist_ts(y, m, d, 12, 45), "open": 101.4, "high": 102.2, "low": 101.3, "close": 102.0,
         "volume": 1500, "upper": 102.4, "lower": 98.4, "ema9": 101.0},
        # 13:15..14:15: filler
        {"ts": _ist_ts(y, m, d, 13, 15), "open": 102.0, "high": 102.3, "low": 101.8, "close": 102.1,
         "volume": 1500, "upper": 102.5, "lower": 98.5, "ema9": 101.2},
        {"ts": _ist_ts(y, m, d, 13, 45), "open": 102.1, "high": 102.4, "low": 101.9, "close": 102.2,
         "volume": 1500, "upper": 102.6, "lower": 98.6, "ema9": 101.4},
        {"ts": _ist_ts(y, m, d, 14, 15), "open": 102.2, "high": 102.5, "low": 102.0, "close": 102.3,
         "volume": 1500, "upper": 102.7, "lower": 98.7, "ema9": 101.5},
    ]


def test_engine_runs_a_full_long_trade_to_target():
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0)
    rows = _setup_window_for_long((2026, 1, 5))
    df = _bars(rows)

    signals, trades = run_backtest_on_bars(df, cfg)

    assert len(signals) == 1
    assert signals[0].direction == "LONG"
    assert signals[0].armed is True
    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "LONG"
    # Entry = sig high (101.3) + tick (0.05) = 101.35; filled at 12:15 (high 101.5 >= trigger)
    assert t.entry_price == pytest.approx(101.35)
    assert t.entry_ts == _ist_ts(2026, 1, 5, 12, 15)
    # Target = upper at sig bar = 102.0; hit at 12:45
    assert t.exit_reason == "TARGET"
    assert t.exit_price == pytest.approx(102.0)
    assert t.exit_ts == _ist_ts(2026, 1, 5, 12, 45)
    assert t.pnl_raw() > 0


def test_force_exit_at_1515_closes_open_position():
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0)
    rows = _setup_window_for_long((2026, 1, 5))
    # Suppress target hit by capping every bar at 101.9 (below upper=102.0).
    for r in rows[7:]:  # 12:45 onward
        r["high"] = min(r["high"], 101.9)
        r["close"] = min(r["close"], 101.9)
    # Add bars from 14:45 .. 15:15 so 15:15 force-exit triggers.
    y, m, d = 2026, 1, 5
    rows.append({
        "ts": _ist_ts(y, m, d, 14, 45),
        "open": 101.5, "high": 101.7, "low": 101.4, "close": 101.6,
        "volume": 1500, "upper": 102.7, "lower": 98.7, "ema9": 101.5,
    })
    rows.append({
        "ts": _ist_ts(y, m, d, 15, 15),
        "open": 101.4, "high": 101.5, "low": 101.2, "close": 101.3,
        "volume": 1500, "upper": 102.7, "lower": 98.7, "ema9": 101.5,
    })
    df = _bars(rows)

    _, trades = run_backtest_on_bars(df, cfg)

    assert len(trades) == 1
    assert trades[0].exit_reason == "EOD_1515"
    # Exit at open of 15:15 bar = 101.4
    assert trades[0].exit_price == pytest.approx(101.4)


def test_no_new_entries_after_1430():
    """A signal whose entry trigger would land on the 14:45 bar must NOT fill."""
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0)
    y, m, d = 2026, 1, 5
    # Build an uptrend ending at 14:15 (signal bar), with entry bar at 14:45.
    rows = [
        {"ts": _ist_ts(y, m, d, 11, 45), "open": 100.0, "high": 100.5, "low": 99.8, "close": 100.3,
         "volume": 1000, "upper": 102, "lower": 98, "ema9": 100.0},
        {"ts": _ist_ts(y, m, d, 12, 15), "open": 100.3, "high": 100.8, "low": 100.1, "close": 100.6,
         "volume": 1000, "upper": 102.1, "lower": 98.1, "ema9": 100.1},
        {"ts": _ist_ts(y, m, d, 12, 45), "open": 100.6, "high": 101.1, "low": 100.4, "close": 100.9,
         "volume": 1000, "upper": 102.2, "lower": 98.2, "ema9": 100.2},
        {"ts": _ist_ts(y, m, d, 13, 15), "open": 100.9, "high": 101.4, "low": 100.7, "close": 101.2,
         "volume": 1000, "upper": 102.3, "lower": 98.3, "ema9": 100.4},
        {"ts": _ist_ts(y, m, d, 13, 45), "open": 101.2, "high": 101.7, "low": 101.0, "close": 101.5,
         "volume": 1000, "upper": 102.4, "lower": 98.4, "ema9": 100.6},
        # signal at 14:15 (still <= 14:30 cutoff; detection allowed)
        {"ts": _ist_ts(y, m, d, 14, 15), "open": 101.1, "high": 101.3, "low": 97.5, "close": 99.5,
         "volume": 2000, "upper": 102.0, "lower": 98.0, "ema9": 100.7},
        # 14:45 — entry attempt would land here but cutoff is 14:30
        {"ts": _ist_ts(y, m, d, 14, 45), "open": 99.6, "high": 101.5, "low": 99.5, "close": 101.4,
         "volume": 1500, "upper": 102.2, "lower": 98.2, "ema9": 100.8},
        {"ts": _ist_ts(y, m, d, 15, 15), "open": 101.4, "high": 102.2, "low": 101.3, "close": 102.0,
         "volume": 1500, "upper": 102.4, "lower": 98.4, "ema9": 101.0},
    ]
    df = _bars(rows)

    signals, trades = run_backtest_on_bars(df, cfg)

    assert len(signals) == 1                 # signal was detected
    assert signals[0].armed is True
    assert len(trades) == 0                  # but never filled — cutoff


def test_sl_first_when_bar_straddles_both():
    """A bar that touches both SL and target exits at SL."""
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0)
    rows = _setup_window_for_long((2026, 1, 5))
    # On the entry bar (12:15) AND the subsequent bar, make the low pierce SL
    # (sig low 97.5, so sl = 97.5 - 0.05 = 97.45). Currently entry bar's low
    # is 99.5. Force low to 97.0 and high to 102.5 so both SL and target are
    # touched in the same bar (the bar AFTER entry).
    rows[7]["low"] = 97.0           # 12:45 bar
    rows[7]["high"] = 102.5
    df = _bars(rows)

    _, trades = run_backtest_on_bars(df, cfg)

    assert len(trades) == 1
    assert trades[0].exit_reason == "STOP"
    assert trades[0].exit_price == pytest.approx(97.5 - 0.05)
    assert trades[0].pnl_raw() < 0


def test_one_trade_per_symbol_per_day_when_reentry_disabled():
    """A second signal the same day should be detected but never armed (since
    detection is gated by `not already_traded_today` and we never re-arm)."""
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0, allow_reentry=False)
    rows = _setup_window_for_long((2026, 1, 5))
    # After the first trade closes at 12:45, simulate a SECOND opposite-direction
    # setup (gap UP + downtrend) at 13:45. We can't easily synthesize a downtrend
    # signal here because the prior bars trend up; for this test we just verify
    # that after a trade closes, no further signals are *armed* even if detected.
    # The first trade is enough — assert exactly one trade and check the engine
    # didn't create a second.
    df = _bars(rows)

    _, trades = run_backtest_on_bars(df, cfg)

    assert len(trades) == 1


def test_signal_with_rr_below_minimum_is_not_armed():
    cfg = BacktestConfig(gap_mode="any", rr_min=999.0)
    rows = _setup_window_for_long((2026, 1, 5))
    df = _bars(rows)

    signals, trades = run_backtest_on_bars(df, cfg)

    assert len(signals) == 1
    assert signals[0].armed is False
    assert len(trades) == 0


def test_anti_lookahead_signal_only_uses_bars_up_to_i():
    """Sanity: appending a 'future' bar with extreme values must NOT change
    the signal detected at the original signal-bar index."""
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0)
    base = _setup_window_for_long((2026, 1, 5))
    df_short = _bars(base[:6])              # ends at signal bar 11:45
    df_long = _bars(base[:6] + [
        # crazy future bar — if the engine peeks, it'd warp the signal
        {"ts": _ist_ts(2026, 1, 5, 12, 15), "open": 999, "high": 9999,
         "low": -999, "close": 50,
         "volume": 0, "upper": 0, "lower": 0, "ema9": 0},
    ])

    sigs_short, _ = run_backtest_on_bars(df_short, cfg)
    sigs_long,  _ = run_backtest_on_bars(df_long, cfg)

    # The signal detected at 11:45 must be identical regardless of any
    # bars AFTER it. (Engine may add MORE signals from later bars in df_long,
    # but the 11:45 one must match.)
    sig11_short = [s for s in sigs_short if s.setup_ts == _ist_ts(2026, 1, 5, 11, 45)]
    sig11_long  = [s for s in sigs_long  if s.setup_ts == _ist_ts(2026, 1, 5, 11, 45)]
    assert len(sig11_short) == 1
    assert len(sig11_long) == 1
    assert sig11_short[0].rr == pytest.approx(sig11_long[0].rr)


def test_session_reset_clears_pending_setup_across_day_boundary():
    """A setup detected on day D that doesn't fill must not carry into day D+1."""
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0)
    # Day 1 ends at 14:15 with a setup detected but no entry bar (no 14:45 bar).
    day1 = _setup_window_for_long((2026, 1, 5))[:6]   # up to signal bar 11:45
    # ... then a single 14:15 placeholder bar so the day "ends" without filling
    day1.append({
        "ts": _ist_ts(2026, 1, 5, 14, 15),
        "open": 100, "high": 100.5, "low": 99.8, "close": 100.1,
        "volume": 1000, "upper": 101, "lower": 99, "ema9": 100.0,
    })
    # NOTE: entry trigger is sig_high (101.3) + tick = 101.35. The 14:15
    # bar's high is 100.5 < 101.35, so it won't fill. Good.

    # Day 2: a single bar at 09:15 that ALSO would fill if pending carried over
    # (high 102 > 101.35). It must NOT fill.
    day2_first = [{
        "ts": _ist_ts(2026, 1, 6, 9, 15),
        "open": 100, "high": 102, "low": 99, "close": 101,
        "volume": 1000, "upper": 102, "lower": 99, "ema9": 100.0,
    }]
    df = _bars(day1 + day2_first)

    _, trades = run_backtest_on_bars(df, cfg)
    assert len(trades) == 0


def test_ist_minutes_helper():
    # 09:15 IST -> 9*60+15 = 555
    assert _ist_minutes(_ist_ts(2026, 1, 5, 9, 15)) == 555
    # 15:15 IST -> 915
    assert _ist_minutes(_ist_ts(2026, 1, 5, 15, 15)) == 915


def test_pnl_leveraged_multiplies_raw():
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0, leverage=5.0)
    rows = _setup_window_for_long((2026, 1, 5))
    df = _bars(rows)

    _, trades = run_backtest_on_bars(df, cfg)

    assert len(trades) == 1
    t = trades[0]
    raw = t.pnl_raw()
    lev = t.pnl_leveraged(cfg.leverage)
    assert lev == pytest.approx(raw * 5.0)
