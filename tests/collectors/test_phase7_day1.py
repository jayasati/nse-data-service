"""
Phase 7 Day 1 acceptance tests — 5 Archetype A collectors.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.indices import Indices
from nse_data.collectors.live_equity import LiveEquity
from nse_data.collectors.market_movers import Gainers, Losers
from nse_data.collectors.most_active import MostActiveByValue, MostActiveByVolume

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
# LiveEquity
# ============================================================================

def test_live_equity_normalize_drops_index_row():
    """NIFTY 50 fixture's first row is the index itself (priority=0); drop it."""
    data = _load("live_equity_nifty50.json")
    rows = LiveEquity().normalize(data, Request(path_or_url="x", meta={"index_name": "NIFTY 50"}))
    # 51 rows in fixture; first is the index itself
    assert 40 <= len(rows) <= 60
    # No index appears as a symbol in our output
    assert "NIFTY 50" not in {r["symbol"] for r in rows}


def test_live_equity_runs_end_to_end(db):
    data = _load("live_equity_nifty50.json")
    session = FakeSession(json_fixtures={"/api/equity-stockIndices": data})
    report = LiveEquity().run(session, db)
    assert report.succeeded == 1
    assert report.persist.inserted >= 40

    count = db.execute("SELECT COUNT(*) FROM raw_equity_quotes").fetchone()[0]
    assert count == report.persist.inserted


def test_live_equity_rerun_at_new_timestamp_accumulates(db, monkeypatch):
    data = _load("live_equity_nifty50.json")
    session = FakeSession(json_fixtures={"/api/equity-stockIndices": data})

    fake_now = [1_700_000_000]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])

    LiveEquity().run(session, db)
    fake_now[0] += 60
    LiveEquity().run(session, db)

    distinct = db.execute(
        "SELECT COUNT(DISTINCT as_of) FROM raw_equity_quotes"
    ).fetchone()[0]
    assert distinct == 2


# ============================================================================
# Indices + advances_declines (one collector, two tables)
# ============================================================================

def test_indices_writes_both_tables(db):
    data = _load("indices_allindices.json")
    session = FakeSession(json_fixtures={"/api/allIndices": data})

    report = Indices().run(session, db)
    assert report.succeeded == 1
    # 139 indices in fixture
    assert report.persist.inserted >= 100

    idx_count = db.execute("SELECT COUNT(*) FROM raw_indices").fetchone()[0]
    breadth_count = db.execute("SELECT COUNT(*) FROM raw_advances_declines").fetchone()[0]
    assert idx_count >= 100
    assert breadth_count == 1


def test_indices_breadth_values_match_fixture(db):
    data = _load("indices_allindices.json")
    session = FakeSession(json_fixtures={"/api/allIndices": data})
    Indices().run(session, db)

    row = db.execute(
        "SELECT advances, declines, unchanged FROM raw_advances_declines"
    ).fetchone()
    assert row == (data["advances"], data["declines"], data["unchanged"])


# ============================================================================
# Gainers + Losers
# ============================================================================

def test_gainers_normalize_walks_all_categories():
    data = _load("market_movers_gainers.json")
    rows = Gainers().normalize(data, Request(path_or_url="x", meta={"direction": "gainer"}))
    # 7 categories × several stocks each — expect dozens of rows
    categories = {r["category"] for r in rows}
    # At least NIFTY and allSec should always be present
    assert "NIFTY" in categories
    assert "allSec" in categories
    # All tagged as gainer
    assert all(r["direction"] == "gainer" for r in rows)


def test_losers_tags_direction_correctly():
    data = _load("market_movers_losers.json")
    rows = Losers().normalize(data, Request(path_or_url="x", meta={"direction": "loser"}))
    assert all(r["direction"] == "loser" for r in rows)


def test_gainers_loser_both_persist_with_isolation(db):
    """Run both gainers and losers; verify they coexist in raw_market_movers."""
    gainers_data = _load("market_movers_gainers.json")
    losers_data = _load("market_movers_losers.json")

    session = FakeSession(json_fixtures={
        "/api/live-analysis-variations": gainers_data,
    })
    Gainers().run(session, db)

    # Replace fixture for the losers run (FakeSession routes by URL)
    session2 = FakeSession(json_fixtures={
        "/api/live-analysis-variations": losers_data,
    })
    Losers().run(session2, db)

    g_count = db.execute(
        "SELECT COUNT(*) FROM raw_market_movers WHERE direction='gainer'"
    ).fetchone()[0]
    l_count = db.execute(
        "SELECT COUNT(*) FROM raw_market_movers WHERE direction='loser'"
    ).fetchone()[0]
    assert g_count > 0
    assert l_count > 0


# ============================================================================
# Most Active by Volume + by Value
# ============================================================================

def test_most_active_volume_assigns_rank(db):
    data = _load("most_active_volume.json")
    session = FakeSession(json_fixtures={"/api/live-analysis-most-active-securities": data})
    MostActiveByVolume().run(session, db)

    ranks = [
        r[0] for r in db.execute(
            "SELECT rank FROM raw_most_active WHERE list_type='volume' ORDER BY rank"
        ).fetchall()
    ]
    assert ranks == list(range(1, len(ranks) + 1))


def test_most_active_value_and_volume_coexist(db):
    vol_data = _load("most_active_volume.json")
    val_data = _load("most_active_value.json")

    s1 = FakeSession(json_fixtures={"/api/live-analysis-most-active-securities": vol_data})
    MostActiveByVolume().run(s1, db)

    s2 = FakeSession(json_fixtures={"/api/live-analysis-most-active-securities": val_data})
    MostActiveByValue().run(s2, db)

    vol = db.execute(
        "SELECT COUNT(*) FROM raw_most_active WHERE list_type='volume'"
    ).fetchone()[0]
    val = db.execute(
        "SELECT COUNT(*) FROM raw_most_active WHERE list_type='value'"
    ).fetchone()[0]
    assert vol == 20
    assert val == 20