"""Angel live-quote gap poller (collectors/angel_live_equity.py)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.angel_live_equity import load_gap_symbols, run_angel_live_pass
from nse_data.webcore.config import LIVE_INDEX

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture
def db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript((MIGRATIONS / "006_phase7_day1.sql").read_text())  # raw_equity_quotes
    return c


def _fake_fetch(symbols):
    # Mimics angel.fetch_quotes output; one symbol returns no price (illiquid).
    return [
        {"symbol": "NIFTYBEES", "last_price": 280.5, "volume": 120000,
         "open": 279.0, "day_high": 281.0, "day_low": 278.5, "prev_close": 279.5},
        {"symbol": "MOTISONS", "last_price": 95.2, "volume": 50000,
         "open": 94.0, "day_high": 96.0, "day_low": 93.5, "prev_close": 94.1},
        {"symbol": "DEADTICKER", "last_price": None, "volume": None},  # dropped
    ]


def test_writes_quotes_under_live_index(db):
    rep = run_angel_live_pass(db, ["NIFTYBEES", "MOTISONS", "DEADTICKER"],
                              now=1_700_000_000, fetcher=_fake_fetch)
    assert rep == {"symbols": 3, "fetched": 3, "written": 2}   # DEADTICKER dropped (no price)
    rows = db.execute(
        "SELECT symbol, index_name, last_price, volume FROM raw_equity_quotes "
        "ORDER BY symbol").fetchall()
    assert rows == [
        ("MOTISONS", LIVE_INDEX, 95.2, 50000),
        ("NIFTYBEES", LIVE_INDEX, 280.5, 120000),
    ]


def test_empty_symbols_is_noop(db):
    assert run_angel_live_pass(db, [], fetcher=_fake_fetch) == {"symbols": 0, "written": 0}
    assert db.execute("SELECT COUNT(*) FROM raw_equity_quotes").fetchone()[0] == 0


def test_quotes_feed_the_1min_builder(db):
    """The written rows land under LIVE_INDEX with (symbol, as_of, last_price,
    volume) — exactly what indicators.intraday_ohlcv._build_live_1m reads."""
    run_angel_live_pass(db, ["NIFTYBEES"], now=1_700_000_000, fetcher=_fake_fetch)
    row = db.execute(
        "SELECT as_of, last_price, volume FROM raw_equity_quotes "
        "WHERE index_name = ? AND symbol = 'NIFTYBEES'", (LIVE_INDEX,)).fetchone()
    assert row == (1_700_000_000, 280.5, 120000)


def test_load_gap_symbols(tmp_path):
    f = tmp_path / "syms.txt"
    f.write_text("NIFTYBEES\nmotisons\n\n GOLDBEES \n")
    assert load_gap_symbols(f) == ["NIFTYBEES", "MOTISONS", "GOLDBEES"]
    assert load_gap_symbols(tmp_path / "absent.txt") == []
