"""Tests for R6 valuation-vs-own-history (PE-proxy percentile reconstruction)."""
from __future__ import annotations

import datetime as _dt
import sqlite3

from nse_data.research import valuation_history as vh

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _db(today_close: float, *, quarters: int = 12, days: int = 500):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE raw_intraday_candles (symbol TEXT, interval TEXT, ts INTEGER, close REAL);"
        "CREATE TABLE extracted_financials (symbol TEXT, period_ending TEXT, scope TEXT, pat_cr REAL);")
    # constant quarterly PAT → TTM-PAT = 400 throughout (so V_t ∝ price_t)
    qrows = []
    for i in range(quarters):
        y, (m, dd) = 2022 + i // 4, [(3, "31"), (6, "30"), (9, "30"), (12, "31")][i % 4]
        qrows.append(("X", f"{y}-{m:02d}-{dd}", "standalone", 100.0))
    conn.executemany("INSERT INTO extracted_financials VALUES (?,?,?,?)", qrows)
    # daily closes: a 100..199 sawtooth body, last bar = today_close
    base = int(_dt.datetime(2024, 6, 1, tzinfo=_IST).timestamp())
    crows = [("X", "day", base + i * 86400, float(100 + (i % 100) if i < days - 1 else today_close))
             for i in range(days)]
    conn.executemany("INSERT INTO raw_intraday_candles VALUES (?,?,?,?)", crows)
    conn.commit()
    return conn


def test_cheap_when_today_at_bottom_of_own_range():
    r = vh.valuation_percentile(_db(100.0, days=1200), "X")   # today = body min, ~3.3y span
    assert r is not None
    assert r["pctile"] <= 5 and r["cheap"] is True and r["expensive"] is False
    assert r["span_years"] >= 3.0


def test_expensive_when_today_at_top_of_own_range():
    r = vh.valuation_percentile(_db(199.0, days=1200), "X")   # today = body maximum
    assert r["pctile"] >= 95 and r["expensive"] is True


def test_mid_range_is_neither():
    r = vh.valuation_percentile(_db(150.0, days=1200), "X")   # today mid-band
    assert 25 < r["pctile"] < 75 and not r["cheap"] and not r["expensive"]


def test_thin_history_reports_pctile_but_does_not_assert_cheap():
    # ~1.4y window: percentile still computed, but cheap stays False (short-history caveat)
    r = vh.valuation_percentile(_db(100.0, days=500), "X")
    assert r["pctile"] <= 5 and r["cheap"] is False and r["span_years"] < 3.0


def test_none_without_enough_quarters():
    assert vh.valuation_percentile(_db(150.0, quarters=3), "X") is None   # < 4 quarters → no TTM


def test_none_without_enough_candles():
    assert vh.valuation_percentile(_db(150.0, days=20), "X") is None      # < min_points


def test_none_for_unknown_symbol():
    assert vh.valuation_percentile(_db(150.0), "NOPE") is None
