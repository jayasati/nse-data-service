"""
Incremental compute: a second run should only persist new dates and must
correctly carry forward the rolling-window state via lookback.
"""

from __future__ import annotations

from nse_data.indicators.compute import compute_for_symbol
from nse_data.indicators.trend.sma import SimpleMovingAverage

from .conftest import insert_bhavcopy


def test_second_run_writes_only_new_dates(indicators_db):
    closes = [100.0 + i for i in range(25)]
    insert_bhavcopy(indicators_db, "TEST", closes)

    sma = SimpleMovingAverage()
    first = compute_for_symbol(indicators_db, sma, "TEST")
    assert first.rows_written == 6  # 25 - 19 warm-up

    # Add 3 more days of bhavcopy data.
    insert_bhavcopy(indicators_db, "TEST", [125.0, 126.0, 127.0], start_date="2025-01-26")
    second = compute_for_symbol(indicators_db, sma, "TEST")
    assert second.rows_written == 3

    total = indicators_db.execute(
        "SELECT COUNT(*) FROM indicator_sma WHERE symbol = ?", ("TEST",),
    ).fetchone()[0]
    assert total == 9  # 6 + 3, no duplicates from incremental re-run


def test_incremental_matches_full_recompute(indicators_db):
    """Incremental SMA value on day N must equal a from-scratch recompute."""
    closes = [100.0 + (i * 0.5) for i in range(30)]
    insert_bhavcopy(indicators_db, "TEST", closes)

    sma = SimpleMovingAverage()

    # Full run on the first 25 bars by pretending only 25 exist:
    # we just compute on all 30 in one shot to capture the reference.
    compute_for_symbol(indicators_db, sma, "TEST")
    full_rows = indicators_db.execute(
        "SELECT date, sma_20 FROM indicator_sma WHERE symbol = ? ORDER BY date",
        ("TEST",),
    ).fetchall()

    # Now wipe and rebuild incrementally: first batch of 25, then last 5.
    indicators_db.execute("DELETE FROM indicator_sma WHERE symbol = ?", ("TEST",))
    indicators_db.execute("DELETE FROM raw_bhavcopy_cm WHERE symbol = ?", ("TEST",))
    indicators_db.commit()

    insert_bhavcopy(indicators_db, "TEST", closes[:25])
    compute_for_symbol(indicators_db, sma, "TEST")
    insert_bhavcopy(indicators_db, "TEST", closes[25:], start_date="2025-01-26")
    compute_for_symbol(indicators_db, sma, "TEST")

    inc_rows = indicators_db.execute(
        "SELECT date, sma_20 FROM indicator_sma WHERE symbol = ? ORDER BY date",
        ("TEST",),
    ).fetchall()

    assert inc_rows == full_rows
