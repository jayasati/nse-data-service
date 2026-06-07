"""Tests for Week-13 levels + delivery conviction."""

from __future__ import annotations

import sqlite3
from datetime import date

from nse_data.indicators import delivery_tracker as dt
from nse_data.indicators import levels as lv


# ---- pure: round numbers ---------------------------------------------------

def test_nearest_round_number_tiers():
    assert lv.nearest_round_number(1306) == 1300     # <2000 -> step 100
    assert lv.nearest_round_number(247) == 250       # <500 -> step 50
    assert lv.nearest_round_number(43) == 40         # <100 -> step 10


def test_round_number_failures():
    # rn=100: (102,99) approached & closed below -> fail; others not
    bars = [(102, 99), (101, 103), (98, 97)]
    assert lv.round_number_failures(bars, 100) == 1


# ---- pure: delivery score --------------------------------------------------

def test_conviction_score_matrix():
    assert dt.conviction_score(True, True, 0.0) == 0.8     # accumulation
    assert dt.conviction_score(True, False, 0.0) == 0.3    # distribution
    assert dt.conviction_score(False, True, 0.0) == 0.4    # weak chase
    assert dt.conviction_score(False, False, 0.0) == 0.5   # indeterminate
    assert dt.conviction_score(True, True, 2.5) == 0.9     # z>2 bonus, clamped


def test_delivery_ratio_prefers_qty_over_volume():
    assert dt._delivery_ratio(650, 1000, 70.0) == 0.65     # qty/vol
    assert dt._delivery_ratio(None, None, 70.0) == 0.70    # falls back to pct


# ---- DB: levels ------------------------------------------------------------

def _bhav_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE raw_bhavcopy_cm (date TEXT, symbol TEXT, series TEXT,
            prev_close REAL, high REAL, low REAL, close REAL,
            volume REAL, delivery_qty REAL, delivery_pct REAL);
        CREATE TABLE indicator_levels (symbol TEXT, session_date TEXT, high_52w REAL,
            low_52w REAL, days_since_52w_high INT, days_since_52w_low INT, pdh REAL,
            pdl REAL, range_5d_high REAL, range_5d_low REAL, range_20d_high REAL,
            range_20d_low REAL, nearest_round_number REAL, dist_from_round_pct REAL,
            round_number_prior_failures INT, r1 REAL, r2 REAL, s1 REAL, s2 REAL,
            PRIMARY KEY (symbol, session_date));
        CREATE TABLE delivery_conviction (symbol TEXT, session_date TEXT,
            delivery_ratio REAL, delivery_ratio_5d_avg REAL, delivery_ratio_z_score REAL,
            delivery_trend TEXT, delivery_conviction_score REAL,
            PRIMARY KEY (symbol, session_date));
    """)
    return conn


def test_compute_symbol_levels():
    conn = _bhav_db()
    for i in range(25):
        d = f"2026-05-{i+1:02d}"
        conn.execute("INSERT INTO raw_bhavcopy_cm VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (d, "ZED", "EQ", 100, 110 + i, 95 + i, 105 + i, 1e6, 6e5, 60.0))
    conn.commit()
    out = lv.compute_symbol_levels(conn, "ZED", date(2026, 6, 1))
    assert out["pdh"] == 134 and out["pdl"] == 119          # last bar H/L (i=24)
    assert out["range_5d_high"] == 134                       # max over last 5
    assert out["r1"] is not None and out["s1"] is not None   # pivots present
    assert out["nearest_round_number"] > 0


def test_run_delivery_pass_persists():
    conn = _bhav_db()
    for i in range(21):
        d = f"2026-05-{i+1:02d}"
        up = 100 + i                       # rising closes
        conn.execute("INSERT INTO raw_bhavcopy_cm VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (d, "ZED", "EQ", up - 1, up + 2, up - 2, up, 1e6, 7e5, 70.0))
    conn.commit()
    rep = dt.run_delivery_pass(conn, ["ZED"])
    assert rep["symbols"] == 1
    row = conn.execute(
        "SELECT delivery_ratio, delivery_trend FROM delivery_conviction WHERE symbol='ZED'"
    ).fetchone()
    assert abs(row[0] - 0.7) < 1e-6        # 7e5 / 1e6
