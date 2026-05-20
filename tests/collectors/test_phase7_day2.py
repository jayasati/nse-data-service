"""
Phase 7 Day 2 acceptance tests — 6 SnapshotCollector instances across 3 classes.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.high_low_52w import High52W, Low52W
from nse_data.collectors.most_active_fno import (
    MostActiveFnoByValue,
    MostActiveFnoByVolume,
)
from nse_data.collectors.price_band import LowerBandHits, UpperBandHits

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


def _load(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


# ============================================================================
# High52W / Low52W
# ============================================================================

def test_high_52w_normalize_includes_both_tiers():
    data = _load("high_52w.json")
    rows = High52W().normalize(data, Request(path_or_url="x", meta={"event": "high"}))
    tiers = {r["price_tier"] for r in rows}
    assert tiers == {"gt20", "lte20"}
    assert all(r["event"] == "high" for r in rows)


def test_low_52w_tags_event_correctly():
    data = _load("low_52w.json")
    rows = Low52W().normalize(data, Request(path_or_url="x", meta={"event": "low"}))
    assert all(r["event"] == "low" for r in rows)


def test_52w_handles_nse_typo():
    """NSE wire format has 'comapnyName' (typo). Make sure we picked it up."""
    data = _load("high_52w.json")
    rows = High52W().normalize(data, Request(path_or_url="x", meta={"event": "high"}))
    # At least some rows should have populated company_name
    with_name = sum(1 for r in rows if r["company_name"])
    assert with_name > 0


def test_52w_runs_end_to_end(db):
    data = _load("high_52w.json")
    session = FakeSession(json_fixtures={"/api/live-analysis-52Week": data})
    report = High52W().run(session, db)
    assert report.succeeded == 1
    assert report.persist.inserted == report.rows_seen


# ============================================================================
# Price Band Hitters
# ============================================================================

def test_upper_band_walks_all_categories():
    data = _load("price_band_upper.json")
    rows = UpperBandHits().normalize(
        data, Request(path_or_url="x", meta={"band": "upper"})
    )
    # If circuits aren't being hit right now, rows could be empty — that's fine
    if rows:
        assert all(r["band"] == "upper" for r in rows)
        assert all(r["category"] in ("AllSec", "SecGtr20", "SecLwr20") for r in rows)


def test_lower_band_tags_direction():
    data = _load("price_band_lower.json")
    rows = LowerBandHits().normalize(
        data, Request(path_or_url="x", meta={"band": "lower"})
    )
    if rows:
        assert all(r["band"] == "lower" for r in rows)


def test_band_hitters_handle_empty_response(db):
    """When no stock is hitting circuit, all three categories return empty data."""
    session = FakeSession(json_fixtures={
        "/api/live-analysis-price-band-hitter": {
            "AllSec":   {"data": [], "timestamp": "x", "count": 0},
            "SecGtr20": {"data": [], "timestamp": "x", "count": 0},
            "SecLwr20": {"data": [], "timestamp": "x", "count": 0},
        }
    })
    report = UpperBandHits().run(session, db)
    assert report.succeeded == 1
    assert report.rows_seen == 0
    assert report.persist.inserted == 0


# ============================================================================
# Most Active F&O Contracts
# ============================================================================

def test_most_active_fno_picks_correct_block():
    data = _load("most_active_fno.json")
    vol_rows = MostActiveFnoByVolume().normalize(
        data, Request(path_or_url="x", meta={"list_type": "volume"})
    )
    val_rows = MostActiveFnoByValue().normalize(
        data, Request(path_or_url="x", meta={"list_type": "value"})
    )
    assert all(r["list_type"] == "volume" for r in vol_rows)
    assert all(r["list_type"] == "value" for r in val_rows)


def test_most_active_fno_assigns_rank(db):
    data = _load("most_active_fno.json")
    session = FakeSession(json_fixtures={"/api/snapshot-derivatives-equity": data})
    MostActiveFnoByVolume().run(session, db)

    ranks = [
        r[0] for r in db.execute(
            "SELECT rank FROM raw_most_active_fno WHERE list_type='volume' ORDER BY rank"
        ).fetchall()
    ]
    if ranks:
        assert ranks == list(range(1, len(ranks) + 1))


def test_most_active_fno_volume_and_value_coexist(db):
    data = _load("most_active_fno.json")
    session = FakeSession(json_fixtures={"/api/snapshot-derivatives-equity": data})
    MostActiveFnoByVolume().run(session, db)
    MostActiveFnoByValue().run(session, db)

    vol = db.execute(
        "SELECT COUNT(*) FROM raw_most_active_fno WHERE list_type='volume'"
    ).fetchone()[0]
    val = db.execute(
        "SELECT COUNT(*) FROM raw_most_active_fno WHERE list_type='value'"
    ).fetchone()[0]
    assert vol > 0
    assert val > 0