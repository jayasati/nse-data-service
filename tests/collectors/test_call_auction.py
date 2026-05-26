"""
Acceptance tests for the CallAuction collector.

Fixture is a 3-symbol trim of a real /api/live-watch-call-auction response
captured at 15:14 IST (status CLOSED, session 6), covering:
  MCLEODRUSS - flat price across all six sessions
  SHANKARA   - varying session prices, mixed int/float
  LLOYDS     - in the auction set but zero trades (no session_* fields)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.call_auction import CallAuction

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "call_auction.json"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"
ENDPOINT = "/api/live-watch-call-auction"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


@pytest.fixture
def fixture_data():
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def session(fixture_data):
    return FakeSession(json_fixtures={ENDPOINT: fixture_data})


def _rows(fixture_data):
    return CallAuction().normalize(fixture_data, Request(path_or_url="x"))


# ============================================================================
# Unit — normalize()
# ============================================================================

def test_normalize_returns_one_row_per_symbol(fixture_data):
    rows = _rows(fixture_data)
    assert {r["symbol"] for r in rows} == {"MCLEODRUSS", "SHANKARA", "LLOYDS"}


def test_field_mapping_flat_price(fixture_data):
    row = next(r for r in _rows(fixture_data) if r["symbol"] == "MCLEODRUSS")
    assert row["avg_price"] == 75.38
    assert row["total_volume"] == 211029
    assert row["total_turnover"] == 15907366.02
    assert row["session1_price"] == 75.38
    assert row["session1_qty"] == 74612
    assert row["session6_qty"] == 7486


def test_mixed_int_float_session_prices(fixture_data):
    row = next(r for r in _rows(fixture_data) if r["symbol"] == "SHANKARA")
    assert row["session1_price"] == 127.0   # NSE sent int 127
    assert row["session3_price"] == 127.7
    assert isinstance(row["session1_price"], float)
    assert row["avg_price"] == 127.09


def test_zero_trade_symbol_keeps_zero_and_nulls_sessions(fixture_data):
    """LLOYDS is in the auction set but had no trades: keep it (membership is
    the point), zeros preserved, absent session fields -> NULL."""
    row = next(r for r in _rows(fixture_data) if r["symbol"] == "LLOYDS")
    assert row["avg_price"] == 0.0          # real zero, not dropped to None
    assert row["total_volume"] == 0
    assert row["session1_price"] is None    # field absent in payload
    assert row["session1_qty"] is None


def test_all_rows_share_captured_at(fixture_data):
    assert len({r["captured_at"] for r in _rows(fixture_data)}) == 1


def test_numeric_types(fixture_data):
    for r in _rows(fixture_data):
        for n in range(1, 7):
            if r[f"session{n}_qty"] is not None:
                assert isinstance(r[f"session{n}_qty"], int)
            if r[f"session{n}_price"] is not None:
                assert isinstance(r[f"session{n}_price"], float)


def test_drops_empty_symbol():
    payload = {"data": [{"symbol": ""}, {"symbol": "  "}, {"not_symbol": 1}]}
    assert CallAuction().normalize(payload, Request(path_or_url="x")) == []


def test_handles_empty_and_malformed():
    c = CallAuction()
    assert c.normalize([], Request(path_or_url="x")) == []
    assert c.normalize({}, Request(path_or_url="x")) == []
    assert c.normalize(None, Request(path_or_url="x")) == []
    assert c.normalize({"data": None}, Request(path_or_url="x")) == []


def test_request_targets_correct_endpoint():
    reqs = CallAuction().plan()
    assert len(reqs) == 1
    assert reqs[0].path_or_url == ENDPOINT
    assert "stocks-in-call-auction" in (reqs[0].referer or "")


# ============================================================================
# Integration — reference (diff) semantics: membership add / remove
# ============================================================================

def test_run_inserts_all_rows(session, db):
    report = CallAuction().run(session, db)
    assert report.rows_seen == 3
    assert report.persist.inserted == 3
    assert db.execute("SELECT COUNT(*) FROM raw_call_auction").fetchone()[0] == 3


def test_symbol_leaving_auction_is_removed(fixture_data, db):
    """A symbol present yesterday but absent today must be deleted — that's
    the 'stopped being illiquid' signal Layer 6 reads."""
    CallAuction().run(FakeSession(json_fixtures={ENDPOINT: fixture_data}), db)

    # Next day: LLOYDS has left the call-auction set.
    shrunk = {**fixture_data,
              "data": [r for r in fixture_data["data"] if r["symbol"] != "LLOYDS"]}
    report = CallAuction().run(FakeSession(json_fixtures={ENDPOINT: shrunk}), db)

    assert report.persist.removed == 1
    remaining = {r[0] for r in db.execute("SELECT symbol FROM raw_call_auction")}
    assert "LLOYDS" not in remaining
    assert remaining == {"MCLEODRUSS", "SHANKARA"}


def test_new_symbol_entering_is_inserted(fixture_data, db):
    CallAuction().run(FakeSession(json_fixtures={ENDPOINT: fixture_data}), db)

    grown = {**fixture_data,
             "data": fixture_data["data"] + [{"symbol": "NEWILLIQ",
                                              "avg_price": 10, "total_volume": 5,
                                              "total_turnover": 50}]}
    report = CallAuction().run(FakeSession(json_fixtures={ENDPOINT: grown}), db)

    assert report.persist.inserted == 1
    symbols = {r[0] for r in db.execute("SELECT symbol FROM raw_call_auction")}
    assert "NEWILLIQ" in symbols
