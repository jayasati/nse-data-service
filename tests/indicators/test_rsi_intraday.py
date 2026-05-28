"""Intraday RSI 14 on 5-min bars — math + cadence routing + incremental write."""

from __future__ import annotations

from nse_data.indicators.compute import compute_for_symbol
from nse_data.indicators.momentum.rsi_intraday import RsiIntraday

from .conftest import insert_intraday_candles

# Five 1-min bars collapse into one 5-min bar. To produce N 5-min bars we need
# 5N consecutive minute bars in raw_intraday_candles.
_BAR_SECS_1M = 60
_RESAMPLE_FACTOR = 5

# Some arbitrary intraday epoch (UTC) — Mon 2026-05-26 03:45 UTC ≈ 09:15 IST.
_BASE_TS = 1_779_435_900


def _seed_minutes(conn, symbol, minutes_of_closes):
    """Insert N 1-min bars; returns the list of bar timestamps."""
    return insert_intraday_candles(
        conn, symbol, minutes_of_closes, start_ts=_BASE_TS, bar_seconds=_BAR_SECS_1M,
    )


def test_intraday_rsi_writes_with_ts_pk(indicators_db):
    """Strictly rising 5-min closes → RSI saturates near 100; rows keyed by ts."""
    # 80 five-min bars worth of strictly rising prices → 80 × 5 = 400 minute bars.
    closes = [100.0 + (i // _RESAMPLE_FACTOR) for i in range(80 * _RESAMPLE_FACTOR)]
    _seed_minutes(indicators_db, "TEST", closes)

    result = compute_for_symbol(indicators_db, RsiIntraday(), "TEST")
    assert result.rows_written > 0

    rows = indicators_db.execute(
        "SELECT ts, rsi_14 FROM indicator_rsi_5m WHERE symbol=? ORDER BY ts",
        ("TEST",),
    ).fetchall()
    # PK column is `ts`, not `date` — values are integers, not strings.
    assert isinstance(rows[0][0], int)
    assert rows[-1][1] > 99.0


def test_intraday_rsi_falling_pushes_to_zero(indicators_db):
    closes = [200.0 - (i // _RESAMPLE_FACTOR) for i in range(80 * _RESAMPLE_FACTOR)]
    _seed_minutes(indicators_db, "TEST", closes)

    compute_for_symbol(indicators_db, RsiIntraday(), "TEST")

    last = indicators_db.execute(
        "SELECT rsi_14 FROM indicator_rsi_5m WHERE symbol=? ORDER BY ts DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert last and last[0] < 1.0


def test_intraday_incremental_only_writes_new_bars(indicators_db):
    """Second pass after adding bars only persists the new 5-min rows."""
    closes_a = [100.0 + (i // _RESAMPLE_FACTOR) * 0.5
                for i in range(60 * _RESAMPLE_FACTOR)]
    _seed_minutes(indicators_db, "TEST", closes_a)

    first = compute_for_symbol(indicators_db, RsiIntraday(), "TEST")
    assert first.rows_written > 0
    after_first = indicators_db.execute(
        "SELECT COUNT(*) FROM indicator_rsi_5m WHERE symbol=?", ("TEST",),
    ).fetchone()[0]

    # Append 10 more 5-min worth of minute bars (50 minute bars).
    extra_start = _BASE_TS + 60 * _RESAMPLE_FACTOR * _BAR_SECS_1M
    insert_intraday_candles(
        indicators_db, "TEST",
        [closes_a[-1] + i * 0.5 for i in range(1, 10 * _RESAMPLE_FACTOR + 1)],
        start_ts=extra_start,
        bar_seconds=_BAR_SECS_1M,
    )

    second = compute_for_symbol(indicators_db, RsiIntraday(), "TEST")
    assert second.rows_written > 0
    after_second = indicators_db.execute(
        "SELECT COUNT(*) FROM indicator_rsi_5m WHERE symbol=?", ("TEST",),
    ).fetchone()[0]
    # Second pass adds exactly the new bars on top of the first pass.
    assert after_second == after_first + second.rows_written
