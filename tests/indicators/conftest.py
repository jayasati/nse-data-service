"""
Indicator-suite fixtures.

A real `raw_bhavcopy_cm` schema and a deterministic OHLCV series are all
the indicator tests need. Keeping the helper local to this package avoids
polluting the top-level conftest with indicator-specific tables.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_INPUT_MIGRATIONS = (
    MIGRATIONS_DIR / "003_bhavcopy.sql",           # raw_bhavcopy_cm (EOD source)
    MIGRATIONS_DIR / "025_intraday_candles.sql",   # raw_intraday_candles (intraday source)
)
_INDICATOR_MIGRATIONS = (
    MIGRATIONS_DIR / "026_indicator_sma.sql",
    MIGRATIONS_DIR / "027_indicator_rsi.sql",
    MIGRATIONS_DIR / "028_indicator_macd.sql",
    MIGRATIONS_DIR / "029_indicator_rsi_5m.sql",
    MIGRATIONS_DIR / "030_indicator_macd_5m.sql",
)


@pytest.fixture
def indicators_db() -> sqlite3.Connection:
    """In-memory SQLite with every input + indicator table the suite needs."""
    conn = sqlite3.connect(":memory:")
    for m in _INPUT_MIGRATIONS + _INDICATOR_MIGRATIONS:
        conn.executescript(m.read_text())
    conn.commit()
    return conn


def insert_intraday_candles(
    conn: sqlite3.Connection,
    symbol: str,
    closes: list[float],
    *,
    start_ts: int,
    bar_seconds: int = 60,
    interval: str = "minute",
) -> list[int]:
    """Seed N consecutive 1-minute bars for `symbol` in raw_intraday_candles.

    Each bar is a degenerate candle with O=H=L=C. Returns the list of `ts`
    values used (UTC epoch seconds). The indicator code will resample these
    1-min bars to 5-min.
    """
    timestamps: list[int] = []
    for i, c in enumerate(closes):
        ts = start_ts + i * bar_seconds
        timestamps.append(ts)
        conn.execute(
            "INSERT INTO raw_intraday_candles "
            "(symbol, interval, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, interval, ts, c, c, c, c, 1_000),
        )
    conn.commit()
    return timestamps


def insert_bhavcopy(
    conn: sqlite3.Connection,
    symbol: str,
    closes: list[float],
    *,
    start_date: str = "2025-01-01",
    series: str = "EQ",
) -> list[str]:
    """
    Seed N consecutive daily rows for `symbol`. Dates are calendar days
    (good enough for tests — no holiday calendar needed). Only `close` and
    `date` matter for SMA; the rest of the columns are filled with the same
    close to keep the row valid.
    """
    from datetime import date, timedelta

    dates: list[str] = []
    base = date.fromisoformat(start_date)
    for i, c in enumerate(closes):
        d = (base + timedelta(days=i)).isoformat()
        dates.append(d)
        conn.execute(
            "INSERT INTO raw_bhavcopy_cm "
            "(date, symbol, series, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (d, symbol, series, c, c, c, c, 1_000),
        )
    conn.commit()
    return dates
