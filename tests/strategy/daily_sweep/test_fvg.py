"""Step 5 — Fair Value Gap (3-candle)."""
from __future__ import annotations

import pandas as pd

from nse_data.strategy.daily_sweep.fvg import detect_fvgs


def _bars(rows):  # rows: list of (open, high, low, close)
    idx = pd.date_range("2026-01-01 09:15", periods=len(rows), freq="5min", tz="Asia/Kolkata")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_bullish_fvg():
    # candle1.high=100 < candle3.low=102 → bullish gap [100, 102]
    df = _bars([(99, 100, 98, 99.5), (100, 106, 100, 105), (105, 108, 102, 107)])
    out = detect_fvgs(df)
    last = out.iloc[2]
    assert last["fvg_dir"] == "bull"
    assert last["gap_low"] == 100 and last["gap_high"] == 102 and last["gap_mid"] == 101


def test_bearish_fvg():
    # candle1.low=102 > candle3.high=100 → bearish gap [100, 102]
    df = _bars([(103, 104, 102, 102.5), (101, 101, 95, 96), (98, 100, 94, 95)])
    out = detect_fvgs(df)
    last = out.iloc[2]
    assert last["fvg_dir"] == "bear"
    assert last["gap_low"] == 100 and last["gap_high"] == 102


def test_no_fvg_when_candles_overlap():
    df = _bars([(99, 101, 98, 100), (100, 102, 99, 101), (101, 103, 100, 102)])  # c1.high 101 > c3.low 100
    assert detect_fvgs(df)["fvg_dir"].isna().all()
