"""MACD (12/26/9) — sanity-check sign/magnitude + writer round-trip."""

from __future__ import annotations

import math

from nse_data.indicators.compute import compute_for_symbol
from nse_data.indicators.momentum.macd import MovingAverageConvergenceDivergence

from .conftest import insert_bhavcopy


def test_rising_trend_macd_positive(indicators_db):
    """Strictly rising closes — short EMA leads long EMA → MACD positive."""
    closes = [100.0 + i for i in range(120)]
    insert_bhavcopy(indicators_db, "TEST", closes)

    compute_for_symbol(indicators_db, MovingAverageConvergenceDivergence(), "TEST")

    last = indicators_db.execute(
        "SELECT macd, macd_signal, macd_hist FROM indicator_macd "
        "WHERE symbol=? ORDER BY date DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert last and last[0] is not None
    assert last[0] > 0                # MACD line positive on uptrend
    assert last[1] > 0                # signal positive too
    # On a perfectly linear ramp, line + signal converge to the same value.
    # The invariant that survives saturation is "line never below signal".
    assert last[0] >= last[1] - 1e-9


def test_falling_trend_macd_negative(indicators_db):
    closes = [200.0 - i for i in range(120)]
    insert_bhavcopy(indicators_db, "TEST", closes)

    compute_for_symbol(indicators_db, MovingAverageConvergenceDivergence(), "TEST")

    last = indicators_db.execute(
        "SELECT macd, macd_signal, macd_hist FROM indicator_macd "
        "WHERE symbol=? ORDER BY date DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert last and last[0] < 0 and last[1] < 0


def test_histogram_equals_line_minus_signal(indicators_db):
    closes = [100.0 + math.sin(i / 7) * 5 for i in range(120)]
    insert_bhavcopy(indicators_db, "TEST", closes)

    compute_for_symbol(indicators_db, MovingAverageConvergenceDivergence(), "TEST")

    rows = indicators_db.execute(
        "SELECT macd, macd_signal, macd_hist FROM indicator_macd "
        "WHERE symbol=? AND macd_hist IS NOT NULL ORDER BY date",
        ("TEST",),
    ).fetchall()
    assert rows, "no MACD rows written"
    for macd, sig, hist in rows:
        # pandas-ta rounds; allow 1e-9 slack.
        assert abs(hist - (macd - sig)) < 1e-9
