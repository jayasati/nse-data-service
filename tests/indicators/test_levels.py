"""Unit tests for indicators.levels — floor pivots + prior-session readers."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from nse_data.indicators import levels
from nse_data.scheduler.market_hours import IST


def test_floor_pivots_known_values():
    p = levels.floor_pivots(high=110, low=90, close=100)
    assert p["pivot"] == 100.0
    assert p["r1"] == 110.0 and p["s1"] == 90.0
    assert p["r2"] == 120.0 and p["s2"] == 80.0
    assert p["r3"] == 130.0 and p["s3"] == 70.0


def _epoch(y, m, d, hh, mm) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=IST).timestamp())


def _seed_indices() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_indices (index_symbol TEXT, as_of INTEGER, "
                 "high REAL, low REAL, last REAL, PRIMARY KEY(index_symbol, as_of))")
    # prior session 2026-06-04 (close row) and today 2026-06-05 (partial)
    conn.execute("INSERT INTO raw_indices VALUES ('NIFTY 50', ?, 110, 90, 100)",
                 (_epoch(2026, 6, 4, 15, 25),))
    conn.execute("INSERT INTO raw_indices VALUES ('NIFTY 50', ?, 105, 99, 104)",
                 (_epoch(2026, 6, 5, 9, 20),))
    conn.commit()
    return conn


def test_prior_session_picks_last_completed_day():
    conn = _seed_indices()
    ohlc = levels.prior_session_ohlc(conn, "NIFTY 50", date(2026, 6, 5))
    assert ohlc == (110, 90, 100)        # the 06-04 row, not today's partial


def test_index_pivots_uses_prior_session():
    conn = _seed_indices()
    piv = levels.index_pivots(conn, "NIFTY 50", date(2026, 6, 5))
    assert piv is not None and piv["s1"] == 90.0 and piv["r1"] == 110.0


def test_prior_session_none_when_no_earlier_day():
    conn = _seed_indices()
    # ref_date before any row -> nothing earlier
    assert levels.prior_session_ohlc(conn, "NIFTY 50", date(2026, 6, 4)) is None
