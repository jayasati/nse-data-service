"""Tests for the Week-12 full EOD indicator set + trend_regime upgrade + live fold."""

from __future__ import annotations

import numpy as np
import pandas as pd

from nse_data.indicators.eod_full import EodFullSet
from nse_data.indicators.live_snapshot import build_snapshot
from nse_data.indicators.regime import classify_trend_regime


def _trending_daily(n: int = 300) -> pd.DataFrame:
    """A gently rising synthetic daily series (enough bars for the 252 squeeze)."""
    close = np.linspace(100, 200, n) + np.sin(np.arange(n) / 5) * 2
    dates = pd.date_range("2025-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({
        "open": close - 0.5, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": np.full(n, 1_000_000.0),
    }, index=pd.Index(dates, name="date"))


def test_eod_full_computes_all_columns():
    out = EodFullSet().compute(_trending_daily())
    assert set(EodFullSet.output_columns) <= set(out.columns)
    last = out.iloc[-1]
    assert last["ema9"] > last["ema21"]            # uptrend: fast above slow
    assert last["bb_upper"] > last["bb_lower"]
    assert last["supertrend_dir"] == 1             # rising series -> long
    assert not pd.isna(last["adx"])
    assert not pd.isna(last["bb_squeeze"])         # 300 bars > 252 window


# ---- trend_regime upgrade (task 12.3) --------------------------------------

def test_trend_regime_ema_stack_strong():
    # ema9 > ema21 > sma50 > sma200 -> strong_uptrend (precise stack)
    r = classify_trend_regime(price=110, sma50=100, sma200=90, ema9=109, ema21=105)
    assert r == "strong_uptrend"


def test_trend_regime_ema_not_stacked_demotes():
    # price still above both SMAs, but EMAs not fully stacked -> plain uptrend
    r = classify_trend_regime(price=110, sma50=100, sma200=90, ema9=104, ema21=106)
    assert r == "uptrend"


def test_trend_regime_sma_only_backcompat():
    # no EMAs supplied -> original SMA-only behaviour
    assert classify_trend_regime(110, 100, 90) == "strong_uptrend"


# ---- live fold: indicator_eod -> indicator_live ----------------------------

def test_build_snapshot_surfaces_eod_fields(indicators_db):
    conn = indicators_db
    conn.execute(
        "INSERT INTO indicator_eod (symbol, date, ema9, ema21, bb_upper, bb_lower, "
        "bb_squeeze, adx, supertrend_dir, obv) "
        "VALUES ('ZED','2026-06-05', 105.0, 102.0, 110.0, 95.0, 1, 28.0, 1, 12345.0)"
    )
    conn.commit()
    snap = build_snapshot(conn, "ZED")
    assert snap["ema9"] == 105.0 and snap["ema21"] == 102.0
    assert snap["bb_upper"] == 110.0 and snap["bb_squeeze"] == 1
    assert snap["adx"] == 28.0 and snap["supertrend_direction"] == 1
    assert snap["obv"] == 12345.0
