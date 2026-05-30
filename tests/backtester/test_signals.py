"""Signal detection — long and short setups.

We construct minimal hand-crafted 30-min DataFrames with the indicator columns
already populated (no resample, no pandas_ta). That isolates signal logic
from indicator wiring — those are tested in test_bb_ema9_golden.py.
"""

from __future__ import annotations

import pandas as pd
import pytest

from nse_data.backtester.strategies.bb_ema9_30m.config import BacktestConfig
from nse_data.backtester.strategies.bb_ema9_30m.signals import (
    detect_long_setup,
    detect_short_setup,
    _same_ist_day,
)


def _bars(rows: list[dict]) -> pd.DataFrame:
    """Build a 30-min bars DataFrame from a list of row dicts.

    Required keys per row: ts (epoch s), open, high, low, close, upper, lower,
    ema9. x and y are computed automatically.
    """
    df = pd.DataFrame(rows)
    df = df.set_index("ts")
    df["x"] = df["upper"] - df["ema9"]
    df["y"] = df["ema9"]  - df["lower"]
    return df


def _ist_ts(hh: int, mm: int, day_offset_seconds: int = 0) -> int:
    """09:15 IST on 2026-01-05 == epoch 1767584700.
    Helper builds bar timestamps relative to that base."""
    base = 1767584700  # 2026-01-05 09:15 IST
    return base + (hh - 9) * 3600 + (mm - 15) * 60 + day_offset_seconds


# ============================================================ LONG setup

def _long_window_uptrend_then_gapdown() -> pd.DataFrame:
    """5 trending-up bars (close > ema9, ema9 rising) followed by a
    gap-down bar that pierces the lower BB and closes back above it."""
    # 5 uptrend bars 09:15..11:15 IST. ema9 < close at each.
    rows = [
        # ts, open, high, low, close, upper, lower, ema9
        {"ts": _ist_ts(9, 15),  "open": 100, "high": 101, "low": 99.5, "close": 100.8,
         "upper": 102, "lower": 99, "ema9": 100.0},
        {"ts": _ist_ts(9, 45),  "open": 100.8, "high": 101.5, "low": 100.5, "close": 101.3,
         "upper": 102.2, "lower": 99.2, "ema9": 100.3},
        {"ts": _ist_ts(10, 15), "open": 101.3, "high": 102.0, "low": 101.0, "close": 101.8,
         "upper": 102.5, "lower": 99.5, "ema9": 100.6},
        {"ts": _ist_ts(10, 45), "open": 101.8, "high": 102.4, "low": 101.5, "close": 102.2,
         "upper": 102.8, "lower": 99.7, "ema9": 100.9},
        {"ts": _ist_ts(11, 15), "open": 102.2, "high": 102.8, "low": 102.0, "close": 102.6,
         "upper": 103.0, "lower": 100.0, "ema9": 101.2},
        # 6th bar (the signal/gap-down bar). Open gapped down ~1.4%, pierces lower BB,
        # closes back above. Y > X (lower distance > upper distance).
        # ema9 still 101.2 (rising vs ema9_5_ago which was 100.0). 4/5 last closes > ema9.
        # upper = 103.5; lower = 100.5. ema9 = 101.5. x = 2.0, y = 1.0. Need y > x.
        # Recompute: pick lower=99.0 and upper=102.0 so x=0.5 y=2.5.
        {"ts": _ist_ts(11, 45), "open": 101.1, "high": 101.3, "low": 98.5, "close": 100.0,
         "upper": 102.0, "lower": 99.0, "ema9": 101.5},
    ]
    return _bars(rows)


def test_long_setup_detected_on_clean_gap_down_pierce():
    cfg = BacktestConfig(gap_pct=0.003, gap_mode="any", rr_min=0.0)
    df = _long_window_uptrend_then_gapdown()

    setup = detect_long_setup(df, cfg)

    assert setup is not None
    assert setup.direction == "LONG"
    assert setup.setup_ts == _ist_ts(11, 45)
    # Entry above sig bar high + tick; SL below sig bar low - tick; target=upper
    assert setup.entry_trigger == pytest.approx(101.3 + cfg.tick)
    assert setup.sl            == pytest.approx(98.5  - cfg.tick)
    assert setup.target        == pytest.approx(102.0)
    assert setup.rr > 0


def test_long_setup_skipped_when_no_uptrend():
    cfg = BacktestConfig(gap_mode="any")
    df = _long_window_uptrend_then_gapdown()
    # Flatten EMA9 so the uptrend check fails.
    df["ema9"] = 100.0

    assert detect_long_setup(df, cfg) is None


def test_long_setup_skipped_when_gap_too_small():
    cfg = BacktestConfig(gap_pct=0.10, gap_mode="any")  # demand 10% gap
    df = _long_window_uptrend_then_gapdown()

    assert detect_long_setup(df, cfg) is None


def test_long_setup_skipped_when_y_not_greater_than_x():
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0)
    df = _long_window_uptrend_then_gapdown()
    # Make X > Y by widening upper and tightening lower.
    df.loc[df.index[-1], "upper"] = 105.0
    df.loc[df.index[-1], "lower"] = 101.0   # > ema9 which would also break geometry
    df["x"] = df["upper"] - df["ema9"]
    df["y"] = df["ema9"]  - df["lower"]

    assert detect_long_setup(df, cfg) is None


def test_long_setup_skipped_when_lower_band_not_pierced():
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0)
    df = _long_window_uptrend_then_gapdown()
    # Lift the low above the lower BB.
    df.loc[df.index[-1], "low"] = 99.5  # lower is 99.0 so no pierce

    assert detect_long_setup(df, cfg) is None


def test_long_setup_skipped_when_close_below_lower_band():
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0)
    df = _long_window_uptrend_then_gapdown()
    # Force close to stay BELOW the lower band (no rejection).
    df.loc[df.index[-1], "close"] = 98.8  # lower is 99.0

    assert detect_long_setup(df, cfg) is None


# ============================================================ R:R filter

def test_setup_with_rr_below_filter_still_returned_with_rr():
    """detect_*_setup itself doesn't apply rr_min — it returns the Setup with
    rr populated. The ENGINE decides whether to arm it as pending. This test
    documents that contract."""
    cfg = BacktestConfig(gap_mode="any", rr_min=999.0)
    df = _long_window_uptrend_then_gapdown()

    setup = detect_long_setup(df, cfg)
    assert setup is not None       # still returned
    assert setup.rr < 999.0        # but rr is low — engine will skip it


# ============================================================ overnight mode

def test_long_setup_overnight_mode_rejects_intraday_gap():
    cfg = BacktestConfig(gap_mode="overnight", rr_min=0.0)
    df = _long_window_uptrend_then_gapdown()
    # All bars are within the same IST day → overnight mode rejects.

    assert detect_long_setup(df, cfg) is None


def test_long_setup_overnight_mode_accepts_first_bar_of_new_day():
    cfg = BacktestConfig(gap_mode="overnight", rr_min=0.0)
    df = _long_window_uptrend_then_gapdown()
    # Move just the LAST bar to the next IST day (09:15 of D+1).
    new_ts = _ist_ts(9, 15) + 86400
    new_index = list(df.index)
    new_index[-1] = new_ts
    df.index = pd.Index(new_index, name="ts")

    setup = detect_long_setup(df, cfg)
    assert setup is not None
    assert setup.direction == "LONG"


# ============================================================ SHORT setup

def _short_window_downtrend_then_gapup() -> pd.DataFrame:
    rows = [
        {"ts": _ist_ts(9, 15),  "open": 100, "high": 100.5, "low": 99.0, "close": 99.2,
         "upper": 101, "lower": 98, "ema9": 100.0},
        {"ts": _ist_ts(9, 45),  "open": 99.2, "high": 99.5, "low": 98.5, "close": 98.7,
         "upper": 100.8, "lower": 97.8, "ema9": 99.7},
        {"ts": _ist_ts(10, 15), "open": 98.7, "high": 99.0, "low": 98.0, "close": 98.2,
         "upper": 100.5, "lower": 97.5, "ema9": 99.4},
        {"ts": _ist_ts(10, 45), "open": 98.2, "high": 98.5, "low": 97.5, "close": 97.7,
         "upper": 100.2, "lower": 97.2, "ema9": 99.1},
        {"ts": _ist_ts(11, 15), "open": 97.7, "high": 98.0, "low": 97.2, "close": 97.4,
         "upper": 100.0, "lower": 97.0, "ema9": 98.8},
        # Signal bar: gap UP, pierce upper BB, close back below.
        # Want X > Y. Set upper=101, lower=97.5, ema9=98.5 → x=2.5, y=1.0.
        {"ts": _ist_ts(11, 45), "open": 98.8, "high": 101.5, "low": 98.5, "close": 100.5,
         "upper": 101.0, "lower": 97.5, "ema9": 98.5},
    ]
    return _bars(rows)


def test_short_setup_detected_on_clean_gap_up_pierce():
    cfg = BacktestConfig(gap_pct=0.003, gap_mode="any", rr_min=0.0)
    df = _short_window_downtrend_then_gapup()

    setup = detect_short_setup(df, cfg)

    assert setup is not None
    assert setup.direction == "SHORT"
    assert setup.entry_trigger == pytest.approx(98.5 - cfg.tick)
    assert setup.sl            == pytest.approx(101.5 + cfg.tick)
    assert setup.target        == pytest.approx(97.5)


def test_short_setup_skipped_when_no_downtrend():
    cfg = BacktestConfig(gap_mode="any")
    df = _short_window_downtrend_then_gapup()
    df["ema9"] = 100.0  # flatten — no downtrend

    assert detect_short_setup(df, cfg) is None


def test_short_setup_skipped_when_upper_not_pierced():
    cfg = BacktestConfig(gap_mode="any", rr_min=0.0)
    df = _short_window_downtrend_then_gapup()
    df.loc[df.index[-1], "high"] = 100.5  # below upper (101.0)

    assert detect_short_setup(df, cfg) is None


# ============================================================ helpers

def test_same_ist_day_handles_midnight_ist_correctly():
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    before = int(datetime(2026, 1, 4, 23, 59, tzinfo=ist).timestamp())
    after  = int(datetime(2026, 1, 5,  0,  1, tzinfo=ist).timestamp())
    assert not _same_ist_day(before, after)

    same_day_a = int(datetime(2026, 1, 5,  9, 15, tzinfo=ist).timestamp())
    same_day_b = int(datetime(2026, 1, 5, 15, 15, tzinfo=ist).timestamp())
    assert _same_ist_day(same_day_a, same_day_b)
