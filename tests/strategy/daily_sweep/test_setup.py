"""Steps 6–9 — session filter, gap filter, and setup-engine guards."""
from __future__ import annotations

import pandas as pd

from nse_data.strategy.daily_sweep.config import DailySweepConfig
from nse_data.strategy.daily_sweep.setup import _daily_gap_pct, in_session, scan_setups

CFG = DailySweepConfig()


def _ts(s):
    return pd.Timestamp(s, tz="Asia/Kolkata")


def test_session_windows():
    assert in_session(_ts("2026-06-12 10:00"), CFG.sessions) is True       # morning window
    assert in_session(_ts("2026-06-12 14:00"), CFG.sessions) is True       # afternoon window
    assert in_session(_ts("2026-06-12 12:00"), CFG.sessions) is False      # lunch gap
    assert in_session(_ts("2026-06-12 15:20"), CFG.sessions) is False      # after the close window


def test_daily_gap_pct():
    idx = pd.date_range("2026-06-01", periods=3, freq="D", tz="Asia/Kolkata")
    daily = pd.DataFrame({"open": [100, 103, 100], "high": 0, "low": 0,
                          "close": [100, 100, 100], "volume": 0}, index=idx)
    gaps = _daily_gap_pct(daily)
    assert round(gaps[idx[1].date()], 1) == 3.0      # opened 103 vs prior close 100 → +3%


def test_no_setups_without_data():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert scan_setups(empty, empty, empty, config=CFG, symbol="X") == []


def test_no_setups_when_daily_trend_mixed():
    # flat daily (no HH-HL/LH-LL) → no trend → no setups regardless of 5m action
    idx_d = pd.date_range("2026-01-01", periods=40, freq="D", tz="Asia/Kolkata")
    flat = pd.DataFrame({"open": 100, "high": 101, "low": 99, "close": 100, "volume": 0}, index=idx_d)
    idx_5 = pd.date_range("2026-03-01 09:15", periods=200, freq="5min", tz="Asia/Kolkata")
    m5 = pd.DataFrame({"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}, index=idx_5)
    assert scan_setups(flat, m5, m5, config=CFG, symbol="X") == []
