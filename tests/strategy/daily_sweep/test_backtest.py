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
