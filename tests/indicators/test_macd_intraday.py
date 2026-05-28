"""Intraday MACD on 5-min bars — sign/magnitude + ts-PK writer round-trip."""

from __future__ import annotations

from nse_data.indicators.compute import compute_for_symbol
from nse_data.indicators.momentum.macd_intraday import MacdIntraday

from .conftest import insert_intraday_candles

# Five 1-min bars = one 5-min bar after resample.
_BAR_SECS_1M = 60
_RESAMPLE_FACTOR = 5
_BASE_TS = 1_779_435_900  # arbitrary intraday epoch


def _seed_minutes(conn, symbol, closes):
    return insert_intraday_candles(
        conn, symbol, closes, start_ts=_BASE_TS, bar_seconds=_BAR_SECS_1M,
    )


def test_intraday_macd_rising_trend_positive(indicators_db):
    closes = [100.0 + (i // _RESAMPLE_FACTOR) for i in range(120 * _RESAMPLE_FACTOR)]
    _seed_minutes(indicators_db, "TEST", closes)

    result = compute_for_symbol(indicators_db, MacdIntraday(), "TEST")
    assert result.rows_written > 0

    last = indicators_db.execute(
        "SELECT ts, macd, macd_signal, macd_hist FROM indicator_macd_5m "
        "WHERE symbol=? ORDER BY ts DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert isinstance(last[0], int)            # ts PK is integer
    assert last[1] > 0                          # MACD positive on uptrend
    assert last[2] > 0                          # signal positive
    assert last[1] >= last[2] - 1e-9            # line ≥ signal on persistent rise


def test_intraday_macd_falling_trend_negative(indicators_db):
    closes = [200.0 - (i // _RESAMPLE_FACTOR) for i in range(120 * _RESAMPLE_FACTOR)]
    _seed_minutes(indicators_db, "TEST", closes)

    compute_for_symbol(indicators_db, MacdIntraday(), "TEST")

    last = indicators_db.execute(
        "SELECT macd, macd_signal FROM indicator_macd_5m "
        "WHERE symbol=? ORDER BY ts DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert last[0] < 0 and last[1] < 0
