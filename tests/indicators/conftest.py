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

_BHAVCOPY_MIGRATION = MIGRATIONS_DIR / "003_bhavcopy.sql"
_SMA_MIGRATION = MIGRATIONS_DIR / "026_indicator_sma.sql"


@pytest.fixture
def indicators_db() -> sqlite3.Connection:
    """In-memory SQLite with raw_bhavcopy_cm + indicator_sma tables."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_BHAVCOPY_MIGRATION.read_text())
    conn.executescript(_SMA_MIGRATION.read_text())
    conn.commit()
    return conn


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
