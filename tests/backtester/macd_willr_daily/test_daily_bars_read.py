"""read_daily_bars — wraps indicators/ohlcv.py:read_ohlcv with end_date filter."""

from __future__ import annotations

from nse_data.backtester.strategies.macd_willr_daily.bars import read_daily_bars

from .conftest import insert_daily_closes


def test_reads_eq_series_in_ascending_order(daily_db):
    insert_daily_closes(daily_db, "TEST",
                        start_date="2026-01-05",
                        closes=[100, 101, 102, 103, 104])

    df = read_daily_bars(daily_db, "TEST")

    assert len(df) == 5
    assert list(df.index) == [
        "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
    ]
    assert list(df["close"]) == [100, 101, 102, 103, 104]


def test_start_date_filters_inclusively(daily_db):
    insert_daily_closes(daily_db, "TEST",
                        start_date="2026-01-05",
                        closes=[100, 101, 102, 103, 104])

    df = read_daily_bars(daily_db, "TEST", start_date="2026-01-07")

    assert len(df) == 3
    assert df.index[0] == "2026-01-07"


def test_end_date_filters_inclusively(daily_db):
    insert_daily_closes(daily_db, "TEST",
                        start_date="2026-01-05",
                        closes=[100, 101, 102, 103, 104])

    df = read_daily_bars(daily_db, "TEST", end_date="2026-01-07")

    assert len(df) == 3
    assert df.index[-1] == "2026-01-07"


def test_series_filter_excludes_non_eq(daily_db):
    insert_daily_closes(daily_db, "TEST",
                        start_date="2026-01-05",
                        closes=[100, 101, 102])
    # Add a non-EQ row for the same symbol on a different date — must be excluded.
    daily_db.execute(
        "INSERT INTO raw_bhavcopy_cm "
        "(date, symbol, series, open, high, low, close, volume) "
        "VALUES ('2026-01-08', 'TEST', 'BE', 999, 999, 999, 999, 1000)"
    )
    daily_db.commit()

    df = read_daily_bars(daily_db, "TEST")

    assert len(df) == 3
    assert "2026-01-08" not in df.index
    assert 999 not in df["close"].tolist()


def test_empty_when_symbol_unknown(daily_db):
    df = read_daily_bars(daily_db, "UNKNOWN")
    assert df.empty
