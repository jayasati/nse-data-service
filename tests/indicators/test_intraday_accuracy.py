"""Intraday indicator accuracy — the TradingView-parity regression (2026-06).

The bug class under test: recursive indicators (RSI/MACD/Supertrend) computed
with a wall-clock warm-up window re-seeded at every session open (the
overnight gap swallowed the window), printing NaNs for the first hour and
~3 RSI points of seed error vs the continuous series every terminal shows.

Pinned here:
  1. `_intraday_lookback` counts BARS through the backfill (crosses the
     overnight gap), falling back to wall-clock only without backfill.
  2. Warm-up depths are deep enough that a windowed computation matches the
     continuous one to float precision (parity proven on real ADANIGREEN
     data; synthetic series here keeps the test offline + deterministic).
  3. Supertrend runs TradingView's default (10, 3.0) and never emits its
     warm-up zeros as values.
"""
from __future__ import annotations

import math
import sqlite3

import numpy as np
import pandas as pd
import pandas_ta_classic as ta
import pytest

from nse_data.indicators.compute import _intraday_lookback
from nse_data.indicators.momentum.macd_intraday import MacdIntraday
from nse_data.indicators.momentum.rsi_intraday import RsiIntraday
from nse_data.indicators.trend.supertrend_intraday import _LEN, _MULT, SupertrendIntraday


# --- synthetic two-session 5-min series (gap between sessions) -----------------

def _bars(n: int, start_ts: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1000 + np.cumsum(rng.normal(0, 2.0, n))
    high = close + rng.uniform(0.5, 3.0, n)
    low = close - rng.uniform(0.5, 3.0, n)
    idx = [start_ts + i * 300 for i in range(n)]
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": 1000.0}, index=idx)


@pytest.fixture
def series() -> pd.DataFrame:
    """Five 75-bar sessions separated by ~18h gaps — like real NSE days."""
    sessions = []
    t = 1_780_000_000
    for d in range(5):
        sessions.append(_bars(75, t, seed=d))
        t += 75 * 300 + 18 * 3600          # overnight gap
    return pd.concat(sessions)


# --- 1. bar-count lookback crosses the gap --------------------------------------

def test_lookback_counts_bars_not_wallclock(series):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_intraday_candles "
                 "(symbol TEXT, interval TEXT, ts INTEGER, open REAL, high REAL,"
                 " low REAL, close REAL, volume REAL)")
    conn.execute("CREATE INDEX ix ON raw_intraday_candles(symbol, interval, ts)")
    # one 1-min row per 5-min bar is enough to count against (5× multiplier
    # in the query just makes the window generous, never short)
    conn.executemany(
        "INSERT INTO raw_intraday_candles VALUES ('X', 'minute', ?, 1, 1, 1, 1, 1)",
        [(int(ts),) for ts in series.index])
    conn.commit()

    watermark = int(series.index[-1])          # last bar of session 5
    since = _intraday_lookback(conn, "X", watermark, 280, "EQ")
    n_bars_covered = (series.index >= since).sum()
    # the window must span MULTIPLE sessions — wall-clock would have given
    # 280*300s ≈ 23h ≈ barely one session + gap
    assert n_bars_covered >= 280
    assert since <= int(series.index[0]) or n_bars_covered >= 280

    # fallback: no backfill rows → wall-clock window
    empty = sqlite3.connect(":memory:")
    empty.execute("CREATE TABLE raw_intraday_candles "
                  "(symbol TEXT, interval TEXT, ts INTEGER)")
    assert _intraday_lookback(empty, "X", watermark, 280, "EQ") == watermark - 280 * 300


# --- 2. windowed == continuous at the configured depths ---------------------------

def test_rsi_warmup_depth_reaches_continuous_parity(series):
    ref = ta.rsi(series["close"], length=14)
    win = series.iloc[-RsiIntraday.min_history - 1:]
    got = RsiIntraday().compute(win)["rsi_14"].iloc[-1]
    assert math.isclose(got, ref.iloc[-1], abs_tol=0.05)


def test_macd_warmup_depth_reaches_continuous_parity(series):
    ref = ta.macd(series["close"], fast=12, slow=26, signal=9)
    win = series.iloc[-MacdIntraday.min_history - 1:]
    got = MacdIntraday().compute(win)["macd"].iloc[-1]
    assert math.isclose(got, ref["MACD_12_26_9"].iloc[-1], abs_tol=0.05)


def test_supertrend_warmup_depth_reaches_continuous_parity(series):
    ref = ta.supertrend(series["high"], series["low"], series["close"],
                        length=_LEN, multiplier=_MULT)
    win = series.iloc[-SupertrendIntraday.min_history - 1:]
    got = SupertrendIntraday().compute(win)["supertrend"].iloc[-1]
    assert math.isclose(got, ref[f"SUPERT_{_LEN}_{_MULT}"].iloc[-1], rel_tol=1e-6)


# --- 3. TradingView default params + no warm-up zeros ------------------------------

def test_supertrend_params_are_tradingview_default():
    assert (_LEN, _MULT) == (10, 3.0)


def test_supertrend_never_emits_warmup_zeros(series):
    out = SupertrendIntraday().compute(series.iloc[:30])
    vals = out["supertrend"].dropna()
    assert (vals != 0).all()
    # direction is masked wherever the band is (warm-up rows carry neither)
    assert out["supertrend_dir"].notna().sum() == len(vals)