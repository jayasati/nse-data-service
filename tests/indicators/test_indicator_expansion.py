"""The 12-indicator toolkit (2026-06 expansion) — math + contract regressions.

VWAP/Volume/Structure/CPR/OI/RS/EMA/Supertrend/RSI/Bollinger/MACD/ATR: this
pins the newly added pieces — CPR's textbook formulas, market structure's
no-look-ahead swing confirmation and HH-HL state, the RS line against its
benchmark, the OI×price buildup matrix — and that every family is registered
on the cadences it claims.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from nse_data.indicators.registry import INDICATORS
from nse_data.indicators.relative_strength import RelativeStrengthLine
from nse_data.indicators.trend.cpr import CentralPivotRange
from nse_data.indicators.trend.market_structure import MarketStructure
from nse_data.indicators.volume.open_interest import OpenInterestEod


def _ohlcv(rows: list[tuple[float, float, float]], dates=None) -> pd.DataFrame:
    """rows = (high, low, close); open=close, volume constant."""
    idx = dates or [f"2026-01-{i+1:02d}" for i in range(len(rows))]
    h, l, c = zip(*rows)
    return pd.DataFrame({"open": c, "high": h, "low": l, "close": c,
                         "volume": [1000.0] * len(rows)}, index=list(idx))


# --- registry: the full toolkit is wired -----------------------------------------

def test_all_twelve_families_registered():
    names = {i.name for i in INDICATORS}
    for family in ("vwap_5m", "rvol_5m", "structure", "structure_5m", "cpr",
                   "oi", "rs", "ema", "ema_5m", "supertrend_5m", "rsi",
                   "rsi_5m", "bb_5m", "macd", "macd_5m", "atr", "atr_5m"):
        assert family in names, family


def test_registry_tables_are_unique():
    tables = [i.table for i in INDICATORS]
    assert len(tables) == len(set(tables))


# --- CPR: textbook formulas off the PRIOR bar ---------------------------------------

def test_cpr_levels_from_previous_session():
    # prior bar H=110 L=90 C=100 → pivot=100, bc=100, tc=100 (degenerate flat CPR);
    # use distinct numbers: H=112 L=92 C=99 → pivot=101, bc=102, tc=100
    df = _ohlcv([(112, 92, 99), (105, 95, 101)])
    out = CentralPivotRange().compute(df)
    today = out.iloc[1]
    assert today["cpr_pivot"] == pytest.approx(101.0)
    assert today["cpr_bc"] == pytest.approx(102.0)
    assert today["cpr_tc"] == pytest.approx(100.0)
    assert today["cpr_width_pct"] == pytest.approx(2 / 101 * 100, rel=1e-6)
    assert today["r1"] == pytest.approx(2 * 101 - 92)    # 110
    assert today["s1"] == pytest.approx(2 * 101 - 112)   # 90
    assert today["r2"] == pytest.approx(101 + 20)
    assert today["s2"] == pytest.approx(101 - 20)
    assert out.iloc[0].isna().all()        # first bar has no prior session


# --- market structure: confirmation + state ------------------------------------------

def test_structure_swing_confirms_only_after_k_bars():
    # a clear peak at index 5; with K=3 it can only be known at index 8
    rows = [(10, 9, 9.5)] * 5 + [(20, 18, 19)] + [(10, 9, 9.5)] * 6
    out = MarketStructure().compute(_ohlcv(rows))
    assert pd.isna(out["swing_high"].iloc[7])      # not yet confirmed
    assert out["swing_high"].iloc[8] == 20.0       # confirmed exactly at +K
    assert (out["swing_high"].iloc[9:] == 20.0).all()


def test_structure_uptrend_state():
    # a strict zigzag (no tied extremes): swing highs 20→26→30 (HH) and swing
    # lows 12→16 (HL) → uptrend (+1) once the last peak confirms.
    closes = [14, 15, 16, 17, 18, 19, 20, 19, 18, 17, 16, 15, 14, 13, 12,
              13, 14.5, 15.5, 17, 19, 21, 23, 25, 26, 25, 24, 23, 22, 21, 20,
              19.5, 18.5, 17.5, 16, 17.2, 18.2, 20.2, 22, 24, 26.2, 28, 30,
              29, 28.5, 27.5, 27, 26.8]
    rows = [(c + 0.5, c - 0.5, c) for c in closes]
    out = MarketStructure().compute(_ohlcv(rows, dates=[f"d{i:03d}" for i in range(len(rows))]))
    assert out["structure"].iloc[-1] == 1
    assert out["swing_high"].iloc[-1] == 30.5
    assert out["swing_low"].iloc[-1] == 15.5


# --- RS line: benchmark via prepare() -------------------------------------------------

def test_rs_line_against_benchmark():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_bhavcopy_cm (symbol TEXT, series TEXT, date TEXT, close REAL)")
    dates = [f"2026-01-{i+1:02d}" for i in range(80)]
    conn.executemany("INSERT INTO raw_bhavcopy_cm VALUES ('NIFTYBEES','EQ',?,?)",
                     [(d, 250.0) for d in dates])          # flat benchmark
    conn.commit()
    rs = RelativeStrengthLine()
    rs.prepare(conn, "X")
    df = pd.DataFrame({"open": 0, "high": 0, "low": 0,
                       "close": np.linspace(500, 580, 80), "volume": 1},
                      index=dates)
    out = rs.compute(df)
    # flat benchmark → RS line is just the (scaled) price: strictly rising
    assert out["rs_line"].iloc[0] == pytest.approx(100 * 500 / 250)
    assert out["rs_line"].is_monotonic_increasing
    assert out["rs_sma20"].notna().iloc[-1]


def test_rs_line_without_benchmark_is_na():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_bhavcopy_cm (symbol TEXT, series TEXT, date TEXT, close REAL)")
    rs = RelativeStrengthLine()
    rs.prepare(conn, "X")
    out = rs.compute(_ohlcv([(10, 9, 9.5)] * 5))
    assert out["rs_line"].isna().all()


# --- OI: the buildup matrix -----------------------------------------------------------

@pytest.mark.parametrize("oi_pct,price_steps,expected", [
    (8.0, (100, 103), 1),     # OI↑ price↑ → long buildup
    (8.0, (100, 97), -1),     # OI↑ price↓ → short buildup
    (-8.0, (100, 103), 2),    # OI↓ price↑ → short covering
    (-8.0, (100, 97), -2),    # OI↓ price↓ → long unwinding
    (0.2, (100, 103), 0),     # flat OI → no signal
])
def test_oi_buildup_matrix(oi_pct, price_steps, expected):
    ind = OpenInterestEod()
    dates = ["2026-01-01", "2026-01-02"]
    prev = 50_000
    latest = prev * (1 + oi_pct / 100.0)
    ind._oi_by_date = {dates[1]: (latest, oi_pct)}
    df = pd.DataFrame({"open": price_steps, "high": price_steps, "low": price_steps,
                       "close": price_steps, "volume": 1}, index=dates)
    out = ind.compute(df)
    assert out.loc[dates[1], "oi_buildup"] == expected


def test_oi_prepare_takes_days_last_snapshot():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_oi_spurts (symbol TEXT, as_of INTEGER, latest_oi REAL, prev_oi REAL)")
    day_ist = 1_780_000_000 - (1_780_000_000 % 86400)   # some UTC day start
    conn.executemany("INSERT INTO raw_oi_spurts VALUES ('X', ?, ?, 50000)", [
        (day_ist + 5 * 3600, 51000),     # morning snapshot
        (day_ist + 9 * 3600, 56000),     # afternoon snapshot — must win
    ])
    conn.commit()
    ind = OpenInterestEod()
    ind.prepare(conn, "X")
    assert len(ind._oi_by_date) == 1
    (oi, pct), = ind._oi_by_date.values()
    assert oi == 56000.0
    assert pct == pytest.approx(12.0)