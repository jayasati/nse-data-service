"""Intraday ADX/DI on 5-min bars — direction split + ts-PK writer round-trip."""

from __future__ import annotations

from nse_data.indicators.compute import compute_for_symbol
from nse_data.indicators.trend.adx_intraday import AdxIntraday

from .conftest import insert_intraday_candles

# Five 1-min bars = one 5-min bar after resample.
_BAR_SECS_1M = 60
_RESAMPLE_FACTOR = 5
_BASE_TS = 1_779_435_900  # arbitrary intraday epoch


def _seed_minutes(conn, symbol, closes):
    return insert_intraday_candles(
        conn, symbol, closes, start_ts=_BASE_TS, bar_seconds=_BAR_SECS_1M,
    )


def test_intraday_adx_uptrend_di_plus_dominant(indicators_db):
    # Steady +1/bar staircase: every 5-min bar's high is above the previous
    # high and no low undercuts, so +DM prints every bar and −DM never does.
    closes = [100.0 + (i // _RESAMPLE_FACTOR) for i in range(150 * _RESAMPLE_FACTOR)]
    _seed_minutes(indicators_db, "TEST", closes)

    result = compute_for_symbol(indicators_db, AdxIntraday(), "TEST")
    assert result.rows_written > 0

    last = indicators_db.execute(
        "SELECT ts, adx, di_plus, di_minus FROM indicator_adx_5m "
        "WHERE symbol=? ORDER BY ts DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert isinstance(last[0], int)            # ts PK is integer
    assert last[2] > last[3]                   # +DI dominant on an uptrend
    assert last[1] > 25                        # one-way trend reads as strong


def test_intraday_adx_downtrend_di_minus_dominant(indicators_db):
    closes = [300.0 - (i // _RESAMPLE_FACTOR) for i in range(150 * _RESAMPLE_FACTOR)]
    _seed_minutes(indicators_db, "TEST", closes)

    compute_for_symbol(indicators_db, AdxIntraday(), "TEST")

    last = indicators_db.execute(
        "SELECT adx, di_plus, di_minus FROM indicator_adx_5m "
        "WHERE symbol=? ORDER BY ts DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert last[2] > last[1]                   # −DI dominant on a downtrend
    assert last[0] > 25                        # one-way trend reads as strong
