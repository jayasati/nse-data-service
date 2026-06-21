"""Step 2 (1H retracement zone) + Step 4 (BOS confirmation)."""
from __future__ import annotations

import pandas as pd

from nse_data.strategy.daily_sweep.structure import bos_after, retracement_zone


def _frame(closes, freq="60min"):
    idx = pd.date_range("2026-01-01 09:15", periods=len(closes), freq=freq, tz="Asia/Kolkata")
    return pd.DataFrame({"open": closes, "high": [c + 0.5 for c in closes],
                         "low": [c - 0.5 for c in closes], "close": closes,
                         "volume": [1000] * len(closes)}, index=idx)


# leg: dip to 100 (swing low) → rise to 120 (swing high) → pullback (last close varies)
_LEG = [106, 104, 102, 100] + list(range(102, 121, 2)) + [118, 115, 112]


def test_price_inside_retracement_band_is_in_zone():
    z = retracement_zone(_frame(_LEG + [110]), trend="bullish", fib_min=0.382, fib_max=0.79, k=3)
    assert z["leg_high"] == 120.5 and z["leg_low"] == 99.5
    assert z["zone_low"] < 110 < z["zone_high"] and z["in_zone"] is True


def test_shallow_pullback_not_in_zone():
    # 119 has retraced < 38.2% of the leg → above the band → not in zone
    z = retracement_zone(_frame(_LEG + [119]), trend="bullish", fib_min=0.382, fib_max=0.79, k=3)
    assert z["in_zone"] is False


def test_daily_structure_violation_blocks_zone():
    # price 110 is in the band, but the daily swing low is 115 → trend broken → not valid
    z = retracement_zone(_frame(_LEG + [110]), trend="bullish", fib_min=0.382, fib_max=0.79, k=3,
                         daily_swing_low=115)
    assert z["in_zone"] is False


def test_bullish_bos_breaks_the_lower_high():
    # swing high 100 (idx2), swing low 88 (idx7); a later close > 100 confirms bullish BOS
    m5 = _frame([95, 98, 100, 98, 95, 92, 90, 88, 91, 95, 99, 103, 104], freq="5min")
    bos = bos_after(m5, k=2, sweep_idx=7, direction="bull")
    assert bos is not None and bos["broken_level"] == 100.5
    assert bos["bos_index"] == 11                      # first close (103) above 100.5


def test_no_bos_when_price_stays_below_structure():
    m5 = _frame([95, 98, 100, 98, 95, 92, 90, 88, 91, 95, 97, 98], freq="5min")  # never closes > 100
    assert bos_after(m5, k=2, sweep_idx=7, direction="bull") is None
