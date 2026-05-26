"""
Acceptance tests for the MacroCollector (Yahoo Finance via httpx).

This collector is external (not the NSE session) and overrides run(), so we
test normalize() against real captured Yahoo chart JSON and exercise run() with
a fetch() override (no network). now_ist is patched for a deterministic
as_of_date.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from nse_data.collectors import macro as macro_mod
from nse_data.collectors.base import Request
from nse_data.collectors.macro import MacroCollector
from nse_data.scheduler.market_hours import IST


FIX_DIR = Path(__file__).parent.parent / "fixtures"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(
        macro_mod, "now_ist", lambda: datetime(2026, 5, 26, 18, 0, tzinfo=IST)
    )


@pytest.fixture
def usdinr():
    return json.loads((FIX_DIR / "yahoo_usdinr.json").read_text())


@pytest.fixture
def sp500():
    return json.loads((FIX_DIR / "yahoo_sp500.json").read_text())


# ============================================================================
# normalize()
# ============================================================================

def test_normalize_maps_and_computes_change(usdinr):
    row = MacroCollector().normalize(
        usdinr, Request(path_or_url="x", meta={"asset": "USDINR"})
    )[0]
    assert row["asset"] == "USDINR"
    assert row["as_of_date"] == "2026-05-26"
    assert row["price"] == 95.67
    assert row["prev_close"] == 96.5658
    assert row["change"] == round(95.67 - 96.5658, 4)
    assert row["pct_change"] == round((95.67 - 96.5658) / 96.5658 * 100, 4)
    assert row["currency"] == "INR"
    assert isinstance(row["market_time"], int)


def test_normalize_index_fixture(sp500):
    row = MacroCollector().normalize(
        sp500, Request(path_or_url="x", meta={"asset": "SP500"})
    )[0]
    assert row["asset"] == "SP500"
    assert row["currency"] == "USD"
    assert row["price"] > 0


def test_normalize_malformed_returns_empty():
    c = MacroCollector()
    req = Request(path_or_url="x", meta={"asset": "USDINR"})
    assert c.normalize({}, req) == []
    assert c.normalize({"chart": {"result": []}}, req) == []
    assert c.normalize({"chart": {"result": [{"meta": None}]}}, req) == []
    assert c.normalize(None, req) == []


# ============================================================================
# run() — fetch overridden, no network
# ============================================================================

def _collector_with(fixtures: dict, errors: dict | None = None):
    errors = errors or {}

    class _TestMacro(MacroCollector):
        tickers = {"USDINR": "USDINR=X", "SP500": "^GSPC"}

        def fetch(self, client, symbol):
            if symbol in errors:
                raise errors[symbol]
            return fixtures[symbol]

    return _TestMacro()


def test_run_persists_all_tickers(db, usdinr, sp500):
    c = _collector_with({"USDINR=X": usdinr, "^GSPC": sp500})
    report = c.run(session=None, db=db)
    assert report.fetched == 2
    assert report.succeeded == 2
    assert report.persist.inserted == 2
    rows = dict(db.execute("SELECT asset, currency FROM raw_macro").fetchall())
    assert rows == {"USDINR": "INR", "SP500": "USD"}


def test_run_isolates_a_failing_ticker(db, usdinr):
    c = _collector_with(
        {"USDINR=X": usdinr},
        errors={"^GSPC": RuntimeError("yahoo 502")},
    )
    report = c.run(session=None, db=db)
    assert report.succeeded == 1
    assert report.failed == 1
    assert report.persist.inserted == 1   # USDINR still persisted
    assert {r[0] for r in db.execute("SELECT asset FROM raw_macro")} == {"USDINR"}


def test_rerun_same_day_upserts(db, usdinr, sp500):
    c = _collector_with({"USDINR=X": usdinr, "^GSPC": sp500})
    c.run(session=None, db=db)
    r2 = c.run(session=None, db=db)
    # Same (asset, as_of_date) -> upsert, not duplicate rows.
    assert db.execute("SELECT COUNT(*) FROM raw_macro").fetchone()[0] == 2
    assert r2.persist.inserted == 0
