"""
Phase 3 acceptance tests for the OiSpurts collector.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.oi_spurts import OiSpurts

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "oi_spurts.json"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


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
    return FakeSession(json_fixtures={
        "/api/live-analysis-oi-spurts-underlyings": fixture_data
    })


# ============================================================================
# Unit — normalize()
# ============================================================================

def test_normalize_drops_rows_with_empty_symbol(fixture_data):
    rows = OiSpurts().normalize(fixture_data, Request(path_or_url="x"))
    assert len(rows) == 3
    assert all(r["symbol"] for r in rows)


def test_normalize_all_rows_share_as_of(fixture_data):
    rows = OiSpurts().normalize(fixture_data, Request(path_or_url="x"))
    timestamps = {r["as_of"] for r in rows}
    assert len(timestamps) == 1


def test_normalize_maps_fields_correctly(fixture_data):
    rows = OiSpurts().normalize(fixture_data, Request(path_or_url="x"))
    zyduslife = next(r for r in rows if r["symbol"] == "ZYDUSLIFE")
    assert zyduslife["latest_oi"] == 29321
    assert zyduslife["prev_oi"] == 21894
    assert zyduslife["change_in_oi"] == 7427
    assert zyduslife["avg_oi_pct"] == 33.92
    assert zyduslife["volume"] == 116778
    assert zyduslife["underlying_value"] == 1017.0


def test_normalize_handles_negative_change(fixture_data):
    """OI can shrink — TCS in the fixture has changeInOI=-3200, avgInOI=-4.56."""
    rows = OiSpurts().normalize(fixture_data, Request(path_or_url="x"))
    tcs = next(r for r in rows if r["symbol"] == "TCS")
    assert tcs["change_in_oi"] == -3200
    assert tcs["avg_oi_pct"] == -4.56


def test_normalize_numeric_types(fixture_data):
    rows = OiSpurts().normalize(fixture_data, Request(path_or_url="x"))
    for r in rows:
        # OI counts are integers (contracts can't be fractional)
        if r["latest_oi"] is not None:
            assert isinstance(r["latest_oi"], int)
        if r["change_in_oi"] is not None:
            assert isinstance(r["change_in_oi"], int)
        if r["volume"] is not None:
            assert isinstance(r["volume"], int)
        # Percentages and spot prices are floats
        if r["avg_oi_pct"] is not None:
            assert isinstance(r["avg_oi_pct"], float)
        if r["underlying_value"] is not None:
            assert isinstance(r["underlying_value"], float)


def test_normalize_handles_bare_list(fixture_data):
    bare = fixture_data["data"]
    rows = OiSpurts().normalize(bare, Request(path_or_url="x"))
    assert len(rows) == 3   # still drops empty-symbol row


def test_normalize_handles_empty_response():
    assert OiSpurts().normalize([], Request(path_or_url="x")) == []
    assert OiSpurts().normalize({}, Request(path_or_url="x")) == []
    assert OiSpurts().normalize(None, Request(path_or_url="x")) == []


# ============================================================================
# Integration — snapshot semantics
# ============================================================================

def test_run_inserts_all_normalized_rows(session, db):
    report = OiSpurts().run(session, db)
    assert report.fetched == 1
    assert report.succeeded == 1
    assert report.rows_seen == 3
    assert report.persist.inserted == 3

    count = db.execute("SELECT COUNT(*) FROM raw_oi_spurts").fetchone()[0]
    assert count == 3


def test_three_runs_with_advancing_time_accumulate(fixture_data, db, monkeypatch):
    """Phase 3 contract: 3x runs with advancing as_of -> 3x rows, no collisions."""
    session = FakeSession(json_fixtures={
        "/api/live-analysis-oi-spurts-underlyings": fixture_data
    })

    fake_now = [1_700_000_000]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])

    for _ in range(3):
        OiSpurts().run(session, db)
        fake_now[0] += 60

    total = db.execute("SELECT COUNT(*) FROM raw_oi_spurts").fetchone()[0]
    distinct = db.execute(
        "SELECT COUNT(DISTINCT as_of) FROM raw_oi_spurts"
    ).fetchone()[0]
    assert total == 9   # 3 rows × 3 runs
    assert distinct == 3


def test_same_second_repoll_is_unchanged(fixture_data, db, monkeypatch):
    session = FakeSession(json_fixtures={
        "/api/live-analysis-oi-spurts-underlyings": fixture_data
    })
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000)

    OiSpurts().run(session, db)
    r2 = OiSpurts().run(session, db)

    assert r2.persist.inserted == 0
    assert r2.persist.unchanged == 3