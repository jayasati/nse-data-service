"""SMA indicator: math + writer round-trip."""

from __future__ import annotations

from nse_data.indicators.compute import compute_for_symbol
from nse_data.indicators.trend.sma import SimpleMovingAverage

from .conftest import insert_bhavcopy


def test_sma_writes_only_after_window_fills(indicators_db):
    """SMA-20 isn't valid until 20 bars exist; only those rows should land."""
    closes = [100.0 + i for i in range(25)]  # 25 bars
    insert_bhavcopy(indicators_db, "TEST", closes)

    sma = SimpleMovingAverage()
    result = compute_for_symbol(indicators_db, sma, "TEST")

    # 25 bars - 19 warm-up rows for SMA-20 = 6 writable rows.
    assert result.rows_written == 6

    rows = indicators_db.execute(
        "SELECT date, sma_20, sma_50, sma_200 FROM indicator_sma "
        "WHERE symbol = ? ORDER BY date",
        ("TEST",),
    ).fetchall()
    assert len(rows) == 6

    # First SMA-20 row is the mean of closes[0..19].
    expected = sum(closes[:20]) / 20
    assert rows[0][1] == expected
    # SMA-50 and SMA-200 still NaN at this history depth → NULL in SQL.
    assert rows[0][2] is None
    assert rows[0][3] is None


def test_sma_50_emerges_at_50_bars(indicators_db):
    closes = [float(i) for i in range(60)]
    insert_bhavcopy(indicators_db, "TEST", closes)

    compute_for_symbol(indicators_db, SimpleMovingAverage(), "TEST")

    row_at_50 = indicators_db.execute(
        "SELECT sma_50 FROM indicator_sma "
        "WHERE symbol = ? ORDER BY date LIMIT 1 OFFSET ?",
        ("TEST", 30),  # 30 SMA-20 rows precede the first SMA-50 row
    ).fetchone()
    assert row_at_50[0] == sum(range(50)) / 50
