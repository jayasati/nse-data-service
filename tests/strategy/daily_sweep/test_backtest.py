"""Step 10 — trade simulation (target / stop / SL-first / time exit)."""
from __future__ import annotations

import pandas as pd

from nse_data.strategy.daily_sweep.backtest import _simulate
from nse_data.strategy.daily_sweep.setup import SweepSetup


def _m5(bars, start="2026-06-12 09:30"):  # bars: (low, high, close); bar 0 is the entry bar
    idx = pd.date_range(start, periods=len(bars), freq="5min", tz="Asia/Kolkata")
    return pd.DataFrame({"open": [b[2] for b in bars], "high": [b[1] for b in bars],
                         "low": [b[0] for b in bars], "close": [b[2] for b in bars],
                         "volume": 1000}, index=idx)


def _setup(m5, entry=100.0, stop=98.0, target=106.0, qty=100, direction="long"):
    t0 = m5.index[0]
    return SweepSetup(symbol="X", direction=direction, daily_trend="bullish", sweep_time=t0,
                      swept_level=stop, sweep_extreme=stop, bos_time=t0, fvg_low=stop,
                      fvg_high=entry, entry_time=t0, entry_price=entry, stop=stop, target=target,
                      qty=qty, risk_rupees=qty * abs(entry - stop), rr=3.0)


def test_target_hit():
    m5 = _m5([(100, 100, 100), (99, 107, 105)])    # bar1 high 107 ≥ target 106
    r = _simulate(_setup(m5), m5, segment="intraday")
    assert r.exit_reason == "target" and r.exit_price == 106.0 and r.rr_achieved == 3.0


def test_stop_hit():
    m5 = _m5([(100, 100, 100), (97, 101, 99)])     # bar1 low 97 ≤ stop 98
    r = _simulate(_setup(m5), m5, segment="intraday")
    assert r.exit_reason == "stop" and r.rr_achieved == -1.0


def test_sl_first_when_bar_straddles_both():
    m5 = _m5([(100, 100, 100), (97, 107, 103)])    # touches stop AND target → SL-first
    assert _simulate(_setup(m5), m5, segment="intraday").exit_reason == "stop"


def test_time_exit_at_session_end():
    bars = [(100, 100, 100)] + [(99.5, 100.5, 100)] * 80   # never hits; runs to 15:25+
    m5 = _m5(bars, start="2026-06-12 09:30")
    r = _simulate(_setup(m5), m5, segment="intraday")
    assert r.exit_reason == "time"


def test_short_session_exits_same_day_not_future_candle():
    # Regression: entry on a SHORT session (DR/half-day) with NO 15:25 bar, then a candle days
    # later at an absurd price. The fallback must exit at the entry day's LAST bar — not the
    # whole series' iloc[-1] (the bug that produced a fake +72R / ₹75k BHARTIARTL "win").
    day1 = pd.date_range("2024-05-18 09:15", periods=5, freq="5min", tz="Asia/Kolkata")  # ends 09:35
    idx = day1.append(pd.DatetimeIndex(["2026-06-05 15:25"], tz="Asia/Kolkata"))
    rows = [(100, 100, 100)] * 5 + [(1857, 1857, 1857)]      # day1 flat ~100; future spike 1857
    m5 = pd.DataFrame({"open": [r[2] for r in rows], "high": [r[1] for r in rows],
                       "low": [r[0] for r in rows], "close": [r[2] for r in rows],
                       "volume": 1000}, index=idx)
    r = _simulate(_setup(m5), m5, segment="intraday")
    assert r.exit_reason == "time"
    assert r.exit_time.date().isoformat() == "2024-05-18"    # same day, not 2026
    assert r.exit_price == 100.0 and abs(r.rr_achieved) <= 1  # entry-day close, not 1857
