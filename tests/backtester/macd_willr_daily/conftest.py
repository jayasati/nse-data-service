"""Fixtures for the daily swing strategy tests.

The parent tests/backtester/conftest.py only seeds the intraday tables and
backtest tables. Daily strategy needs raw_bhavcopy_cm (migration 003).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

_MIGRATIONS = (
    MIGRATIONS_DIR / "003_bhavcopy.sql",
    MIGRATIONS_DIR / "032_backtests.sql",
    MIGRATIONS_DIR / "033_backtest_signal_tags.sql",
)


@pytest.fixture
def daily_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for m in _MIGRATIONS:
        conn.executescript(m.read_text())
    conn.commit()
    return conn


def insert_daily_bars(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_date: str,
    bars: list[tuple[float, float, float, float, int]],
    series: str = "EQ",
) -> list[str]:
    """Seed N consecutive daily OHLCV rows. Each tuple = (open, high, low, close, volume).
    Returns the list of date strings used."""
    base = date.fromisoformat(start_date)
    dates: list[str] = []
    for i, (o, h, l, c, v) in enumerate(bars):
        d = (base + timedelta(days=i)).isoformat()
        dates.append(d)
        conn.execute(
            "INSERT INTO raw_bhavcopy_cm "
            "(date, symbol, series, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (d, symbol, series, o, h, l, c, v),
        )
    conn.commit()
    return dates


def insert_daily_closes(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_date: str,
    closes: list[float],
    volume: int = 1_000,
) -> list[str]:
    """Seed `closes` as flat O=H=L=C bars."""
    bars = [(c, c, c, c, volume) for c in closes]
    return insert_daily_bars(conn, symbol, start_date=start_date, bars=bars)
