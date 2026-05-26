"""
Acceptance tests for the PreOpen collector.

Fixture is a 5-symbol trim of a real /api/market-data-pre-open?key=ALL
response captured live at 09:07 IST, covering the edge cases:
  HITECHCORP - gap up (+20%), int pChange, EQ
  TIINDIA    - gap down (-0.1), pChange 0, EQ
  HALDER     - ATO sell qty present (atoSellQty=100), float pChange
  BROOKS     - BE series
  VIVIANA    - SM series (SME)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.pre_open import PreOpen

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "pre_open.json"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"
ENDPOINT = "/api/market-data-pre-open"


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


# ============================================================================
# Unit — normalize()
# ============================================================================

def _rows(fixture_data):
    return PreOpen().normalize(fixture_data, Request(path_or_url="x"))


def test_normalize_returns_one_row_per_symbol(fixture_data):
    rows = _rows(fixture_data)
    assert len(rows) == 5
    assert all(r["symbol"] for r in rows)


def test_all_rows_share_as_of(fixture_data):
    timestamps = {r["as_of"] for r in _rows(fixture_data)}
    assert len(timestamps) == 1


def test_field_mapping_gap_up(fixture_data):
    row = next(r for r in _rows(fixture_data) if r["symbol"] == "HITECHCORP")
    assert row["series"] == "EQ"
    assert row["iep"] == 200.16
    assert row["prev_close"] == 166.8
    assert row["change"] == 33.36
    assert row["pct_change"] == 20.0
    assert row["final_price"] == 200.16
    assert row["final_quantity"] == 23967
    assert row["total_traded_volume"] == 23967
    assert row["total_buy_qty"] == 350533
    assert row["total_sell_qty"] == 0
    assert row["ato_buy_qty"] == 0
    assert row["ato_sell_qty"] == 0
    assert row["year_high"] == 223.44
    assert row["year_low"] == 112.0


def test_int_pchange_coerced_to_float(fixture_data):
    """NSE sends pChange as int for round values; we store float consistently."""
    row = next(r for r in _rows(fixture_data) if r["symbol"] == "HITECHCORP")
    assert isinstance(row["pct_change"], float)


def test_gap_down_negative_change(fixture_data):
    row = next(r for r in _rows(fixture_data) if r["symbol"] == "TIINDIA")
    assert row["change"] == -0.1
    assert row["pct_change"] == 0.0
    assert row["iep"] == 3047.5
    assert row["prev_close"] == 3047.6


def test_ato_quantity_captured(fixture_data):
    """HALDER has at-the-open sell interest the headline numbers don't show."""
    row = next(r for r in _rows(fixture_data) if r["symbol"] == "HALDER")
    assert row["ato_sell_qty"] == 100
    assert row["ato_buy_qty"] == 0


def test_series_preserved(fixture_data):
    rows = {r["symbol"]: r for r in _rows(fixture_data)}
    assert rows["BROOKS"]["series"] == "BE"
    assert rows["VIVIANA"]["series"] == "SM"


def test_nse_timestamp_on_every_row(fixture_data):
    rows = _rows(fixture_data)
    assert all(r["nse_timestamp"] == "26-May-2026 09:07:03" for r in rows)


def test_numeric_types(fixture_data):
    for r in _rows(fixture_data):
        for k in ("final_quantity", "total_traded_volume", "total_buy_qty",
                  "total_sell_qty", "ato_buy_qty", "ato_sell_qty"):
            if r[k] is not None:
                assert isinstance(r[k], int), f"{k} should be int"
        for k in ("iep", "prev_close", "change", "pct_change", "year_high"):
            if r[k] is not None:
                assert isinstance(r[k], float), f"{k} should be float"


def test_drops_empty_symbol():
    payload = {"timestamp": "t", "data": [
        {"metadata": {"symbol": ""}, "detail": {"preOpenMarket": {}}},
        {"metadata": {"symbol": "   "}, "detail": {"preOpenMarket": {}}},
    ]}
    assert PreOpen().normalize(payload, Request(path_or_url="x")) == []


def test_handles_empty_and_malformed_responses():
    p = PreOpen()
    assert p.normalize([], Request(path_or_url="x")) == []
    assert p.normalize({}, Request(path_or_url="x")) == []
    assert p.normalize(None, Request(path_or_url="x")) == []
    assert p.normalize({"data": None}, Request(path_or_url="x")) == []


def test_missing_detail_block_still_yields_row():
    """A row with metadata but no preOpenMarket detail must not crash."""
    payload = {"timestamp": "t", "data": [
        {"metadata": {"symbol": "FOO", "iep": 10, "previousClose": 9}},
    ]}
    rows = PreOpen().normalize(payload, Request(path_or_url="x"))
    assert len(rows) == 1
    assert rows[0]["iep"] == 10.0
    assert rows[0]["total_buy_qty"] is None


# ============================================================================
# Integration — snapshot semantics
# ============================================================================

def test_run_inserts_all_rows(session, db):
    report = PreOpen().run(session, db)
    assert report.fetched == 1
    assert report.succeeded == 1
    assert report.rows_seen == 5
    assert report.persist.inserted == 5
    assert db.execute("SELECT COUNT(*) FROM raw_pre_open").fetchone()[0] == 5


def test_same_second_repoll_is_unchanged(fixture_data, db, monkeypatch):
    session = FakeSession(json_fixtures={ENDPOINT: fixture_data})
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000)

    PreOpen().run(session, db)
    r2 = PreOpen().run(session, db)
    assert r2.persist.inserted == 0
    assert r2.persist.unchanged == 5


def test_polls_on_different_days_accumulate(fixture_data, db, monkeypatch):
    session = FakeSession(json_fixtures={ENDPOINT: fixture_data})
    fake_now = [1_700_000_000]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])

    PreOpen().run(session, db)
    fake_now[0] += 86_400   # next trading day's pre-open
    PreOpen().run(session, db)

    total = db.execute("SELECT COUNT(*) FROM raw_pre_open").fetchone()[0]
    distinct = db.execute(
        "SELECT COUNT(DISTINCT as_of) FROM raw_pre_open"
    ).fetchone()[0]
    assert total == 10
    assert distinct == 2


def test_request_targets_correct_endpoint_and_params():
    reqs = PreOpen().plan()
    assert len(reqs) == 1
    assert reqs[0].path_or_url == ENDPOINT
    assert reqs[0].params == {"key": "ALL"}
    assert "pre-open-market" in (reqs[0].referer or "")
