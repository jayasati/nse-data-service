"""Test the Week-15 stock-profile roll-up."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from nse_data.profile import builder

_MIG = Path(__file__).resolve().parents[2] / "migrations"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    # Real schemas for the sources the builder reads (full columns) + the target.
    for m in ("047_stock_fundamentals.sql", "048_patterns.sql",
              "050_stock_profile_daily.sql"):
        conn.executescript((_MIG / m).read_text())
    # indicator_eod needs only the columns the builder pulls — create directly to
    # avoid the indicator_live ALTER coupling in migration 043.
    conn.executescript("""
        CREATE TABLE indicator_eod (symbol TEXT, date TEXT, ema9 REAL, ema21 REAL,
            bb_upper REAL, bb_lower REAL, bb_width REAL, bb_squeeze INT, adx REAL,
            di_plus REAL, di_minus REAL, supertrend REAL, supertrend_dir INT,
            obv REAL, vol_sma20 REAL, volume_ratio REAL);
        INSERT INTO stock_fundamentals (symbol, quality_score, roe, updated_date)
            VALUES ('ZED', 82.0, 20.0, '2026-06-05');
        INSERT INTO indicator_eod (symbol, date, adx, bb_squeeze)
            VALUES ('ZED', '2026-06-05', 28.0, 1);
        INSERT INTO patterns (symbol, pattern_type, ts, session_date) VALUES
            ('ZED','inside_bar', 1, '2026-06-05'),
            ('ZED','bearish_divergence', 1, '2026-06-05');
    """)
    conn.commit()
    return conn


def test_profile_joins_sources_and_pattern_flags():
    conn = _db()
    row = builder.build_profile_row(conn, "ZED", "2026-06-05", "2026-06-05T19:30:00")
    assert row["quality_score"] == 82.0
    assert row["adx"] == 28.0 and row["bb_squeeze"] == 1
    assert row["had_inside_bar"] == 1
    assert row["had_bearish_divergence"] == 1
    assert row["had_volume_dryup"] == 0          # not present today
    assert row["pdh"] is None                    # indicator_levels absent -> NULL


def test_run_profile_pass_persists():
    conn = _db()
    rep = builder.run_profile_pass(conn, ["ZED"], now=_now())
    assert rep["symbols"] == 1
    score = conn.execute(
        "SELECT quality_score FROM stock_profile_daily WHERE symbol='ZED'"
    ).fetchone()[0]
    assert score == 82.0


def _now():
    from datetime import datetime, timezone, timedelta
    return datetime(2026, 6, 5, 19, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
