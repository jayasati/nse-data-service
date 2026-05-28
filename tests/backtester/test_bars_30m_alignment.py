"""The single most important test in the suite.

If 30-min bars don't land on the NSE session grid (09:15, 09:45, ..., 15:15
IST), every subsequent test is meaningless. pd.resample's default
origin='start_day' produces UTC-midnight buckets, which are off by 15 min
from the IST session — these tests pin that down.
"""

from __future__ import annotations

from nse_data.backtester.bars import read_intraday_30m

from .conftest import IST, insert_flat_minutes, ts_to_ist_hhmm


def test_first_bar_of_day_starts_at_0915_ist(backtest_db):
    # 375 minutes = 09:15 -> 15:30 IST (one full NSE session).
    insert_flat_minutes(
        backtest_db, "TEST",
        start_ist=IST(2026, 1, 5, 9, 15),
        closes=[100 + i * 0.01 for i in range(375)],
    )

    df = read_intraday_30m(backtest_db, "TEST")
    bar_starts_ist = [ts_to_ist_hhmm(ts) for ts in df.index.tolist()]

    assert bar_starts_ist[0] == "09:15", (
        f"First bar should start at 09:15 IST, got {bar_starts_ist[0]}. "
        "Resample origin is misaligned."
    )


def test_bars_land_on_30min_session_grid(backtest_db):
    insert_flat_minutes(
        backtest_db, "TEST",
        start_ist=IST(2026, 1, 5, 9, 15),
        closes=[100.0] * 375,
    )

    df = read_intraday_30m(backtest_db, "TEST")
    bar_starts_ist = [ts_to_ist_hhmm(ts) for ts in df.index.tolist()]

    expected = [
        "09:15", "09:45", "10:15", "10:45",
        "11:15", "11:45", "12:15", "12:45",
        "13:15", "13:45", "14:15", "14:45", "15:15",
    ]
    assert bar_starts_ist == expected


def test_resample_aggregates_ohlcv_correctly(backtest_db):
    # 30 bars covering 09:15-09:45 IST: first bucket only.
    bars = [(10.0 + i * 0.1, 10.0 + i * 0.2, 10.0 - i * 0.1, 10.0 + i * 0.05, 100)
            for i in range(30)]
    insert_flat_minutes(  # we'll overwrite with insert_minute_bars
        backtest_db, "TEST", start_ist=IST(2026, 1, 5, 9, 15), closes=[],
    )
    # Use the richer helper — re-import to keep this test self-contained.
    from .conftest import insert_minute_bars
    insert_minute_bars(
        backtest_db, "TEST2",
        start_ist=IST(2026, 1, 5, 9, 15),
        bars=bars,
    )

    df = read_intraday_30m(backtest_db, "TEST2")

    assert len(df) == 1
    row = df.iloc[0]
    assert row["open"]  == bars[0][0]
    assert row["close"] == bars[-1][3]
    assert row["high"]  == max(b[1] for b in bars)
    assert row["low"]   == min(b[2] for b in bars)
    assert row["volume"] == sum(b[4] for b in bars)


def test_flat_zero_volume_bars_are_dropped(backtest_db):
    # 30 real session-minutes followed by 30 "auction artifact" bars
    # (O=H=L=C, volume=0). Only the real bars should produce output.
    real = [(100.0, 100.1, 99.9, 100.0, 500)] * 30
    artifact = [(100.0, 100.0, 100.0, 100.0, 0)] * 30
    from .conftest import insert_minute_bars
    insert_minute_bars(
        backtest_db, "ART",
        start_ist=IST(2026, 1, 5, 9, 15),
        bars=real + artifact,
    )

    df = read_intraday_30m(backtest_db, "ART")
    # 09:15 bucket (real bars) survives; 09:45 bucket (artifact bars) is dropped.
    assert len(df) == 1
    assert ts_to_ist_hhmm(int(df.index[0])) == "09:15"


def test_date_range_filtering(backtest_db):
    # Two sessions of bars: Jan 5 and Jan 6.
    insert_flat_minutes(
        backtest_db, "RANGE",
        start_ist=IST(2026, 1, 5, 9, 15),
        closes=[100.0] * 375,
    )
    insert_flat_minutes(
        backtest_db, "RANGE",
        start_ist=IST(2026, 1, 6, 9, 15),
        closes=[101.0] * 375,
    )

    only_5 = read_intraday_30m(backtest_db, "RANGE",
                               start_date="2026-01-05", end_date="2026-01-05")
    only_6 = read_intraday_30m(backtest_db, "RANGE",
                               start_date="2026-01-06", end_date="2026-01-06")
    both = read_intraday_30m(backtest_db, "RANGE",
                             start_date="2026-01-05", end_date="2026-01-06")

    assert len(only_5) == 13
    assert len(only_6) == 13
    assert len(both)   == 26
    assert only_5["close"].iloc[-1] == 100.0
    assert only_6["close"].iloc[-1] == 101.0
