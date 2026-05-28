"""Backtester-suite fixtures.

Minimal in-memory SQLite with the tables the backtester reads/writes:
- `raw_intraday_candles` (migration 025) — input source for read_intraday_30m.
- `backtest_runs` / `backtest_trades` (migration 032) — persistence target.

The `insert_minute_bars` helper seeds N consecutive 1-min bars starting at a
given IST datetime. Use `IST(yyyy, mm, dd, hh, mm)` to construct anchors that
make IST-clock assertions readable in tests.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_MIGRATIONS = (
    MIGRATIONS_DIR / "025_intraday_candles.sql",
    MIGRATIONS_DIR / "032_backtests.sql",
)

IST_TZ = timezone(timedelta(hours=5, minutes=30))


def IST(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Convenience for building IST datetimes in tests."""
    return datetime(year, month, day, hour, minute, tzinfo=IST_TZ)


def ist_ts(dt: datetime) -> int:
    """IST datetime -> UTC epoch seconds (matches raw_intraday_candles convention)."""
    return int(dt.astimezone(timezone.utc).timestamp())


@pytest.fixture
def backtest_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for m in _MIGRATIONS:
        conn.executescript(m.read_text())
    conn.commit()
    return conn


def insert_minute_bars(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_ist: datetime,
    bars: list[tuple[float, float, float, float, int]],
) -> list[int]:
    """Seed N 1-min OHLCV bars starting at `start_ist`.

    Each tuple = (open, high, low, close, volume). Returns the list of UTC ts.
    """
    ts0 = ist_ts(start_ist)
    timestamps: list[int] = []
    for i, (o, h, l, c, v) in enumerate(bars):
        ts = ts0 + i * 60
        timestamps.append(ts)
        conn.execute(
            "INSERT INTO raw_intraday_candles "
            "(symbol, interval, ts, open, high, low, close, volume) "
            "VALUES (?, 'minute', ?, ?, ?, ?, ?, ?)",
            (symbol, ts, o, h, l, c, v),
        )
    conn.commit()
    return timestamps


def insert_flat_minutes(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_ist: datetime,
    closes: list[float],
    volume: int = 1_000,
) -> list[int]:
    """Seed `closes` as 1-min O=H=L=C bars. Returns the list of UTC ts."""
    bars = [(c, c, c, c, volume) for c in closes]
    return insert_minute_bars(conn, symbol, start_ist=start_ist, bars=bars)


def ts_to_ist_hhmm(ts: int) -> str:
    """UTC epoch -> 'HH:MM' IST. Handy for human-readable assertions."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST_TZ).strftime("%H:%M")
