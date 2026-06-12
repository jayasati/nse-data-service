"""Choppiness Index on 5-min bars — trending vs ranging split + ts-PK round-trip."""

from __future__ import annotations

from nse_data.indicators.compute import compute_for_symbol
from nse_data.indicators.volatility.chop import ChopIntraday

from .conftest import insert_intraday_candles

_BAR_SECS_1M = 60
_RESAMPLE_FACTOR = 5
_BASE_TS = 1_779_435_900  # arbitrary intraday epoch


def test_intraday_chop_low_on_one_way_trend(indicators_db):
    # Monotonic staircase: every bar extends the range, so the path length
    # equals the net range → CHOP pins to its trending extreme (<38.2).
    closes = [100.0 + (i // _RESAMPLE_FACTOR) for i in range(120 * _RESAMPLE_FACTOR)]
    insert_intraday_candles(indicators_db, "TEST", closes, start_ts=_BASE_TS, bar_seconds=_BAR_SECS_1M)

    result = compute_for_symbol(indicators_db, ChopIntraday(), "TEST")
    assert result.rows_written > 0

    last = indicators_db.execute(
        "SELECT ts, chop_14 FROM indicator_chop_5m WHERE symbol=? ORDER BY ts DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert isinstance(last[0], int)
    assert last[1] < 38.2


def test_intraday_chop_high_on_oscillation(indicators_db):
    # A tight A-B-A-B oscillation: long path, near-zero net range → choppy.
    closes = [100.0 + (1.0 if (i // _RESAMPLE_FACTOR) % 2 else 0.0)
              for i in range(120 * _RESAMPLE_FACTOR)]
    insert_intraday_candles(indicators_db, "TEST", closes, start_ts=_BASE_TS, bar_seconds=_BAR_SECS_1M)

    compute_for_symbol(indicators_db, ChopIntraday(), "TEST")

    last = indicators_db.execute(
        "SELECT chop_14 FROM indicator_chop_5m WHERE symbol=? ORDER BY ts DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert last[0] > 61.8
