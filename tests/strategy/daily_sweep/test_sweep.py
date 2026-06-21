"""Step 3 — 5-minute liquidity sweep."""
from __future__ import annotations

import pandas as pd

from nse_data.strategy.daily_sweep.sweep import detect_sweeps


def _frame(sweep_low: float, sweep_vol: float):
    """A clean V (single swing low at 90), monotone legs so no spurious swings, then a final
    bar that dips to `sweep_low` and closes back at 95 with volume `sweep_vol`."""
    lows = list(range(105, 89, -1)) + list(range(91, 114))     # 105→90 (trough) →113, monotone
    rows = [(lo + 0.5, lo + 1, lo, lo + 0.5) for lo in lows]
    rows.append((92, 96, sweep_low, 95))                       # sweep bar
    vols = [1000] * len(lows) + [sweep_vol]
    idx = pd.date_range("2026-01-01 09:15", periods=len(rows), freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = vols
    return df


def test_bullish_sweep_detected():
    out = detect_sweeps(_frame(85, 5000), swing_k=3)
    last = out.iloc[-1]
    assert last["sweep_dir"] == "bull"
    assert last["swept_level"] == 90.0          # the swing low that was raided
    assert last["penetration"] == 5.0           # 90 − 85


def test_volume_gate_blocks_low_volume_sweep():
    # same pierce+reject, but volume below the 20-bar average → not a sweep
    assert detect_sweeps(_frame(85, 500), swing_k=3).iloc[-1]["sweep_dir"] is None


def test_penetration_gate_blocks_shallow_poke():
    # dips only 0.05 below the swing low — under the 0.1%/0.25·ATR threshold → not a sweep
    assert detect_sweeps(_frame(89.95, 5000), swing_k=3).iloc[-1]["sweep_dir"] is None


def test_index_no_volume_still_sweeps():
    # NIFTY/BANKNIFTY spot carry no volume — the volume gate must not veto an otherwise-valid sweep
    df = _frame(85, 0)
    df["volume"] = 0
    assert detect_sweeps(df, swing_k=3).iloc[-1]["sweep_dir"] == "bull"
