"""Phase 7 Day 5 acceptance tests — 5 reference-data collectors."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.fno_list import FnoList
from nse_data.collectors.index_members import IndexMembers
from nse_data.collectors.new_listings import NewListings
from nse_data.collectors.primary_market import PrimaryMarket
from nse_data.collectors.quote_metadata import QuoteMetadata

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
# FnoList
# ============================================================================

def test_fno_list_normalize_extracts_symbols():
    data = _load("probe_fno_list.json")
    rows = FnoList().normalize(data, Request(path_or_url="x"))
    # 209 stocks in fixture
    assert 150 <= len(rows) <= 250
    symbols = {r["symbol"] for r in rows}
    # No index header row
    assert "SECURITIES IN F&O" not in symbols


def test_fno_list_diff_tracks_changes(db):
    data = _load("probe_fno_list.json")
    session = FakeSession(json_fixtures={"/api/equity-stockIndices": data})

    r1 = FnoList().run(session, db)
    assert r1.persist.inserted > 100

    # Re-run with same data: unchanged
    r2 = FnoList().run(session, db)
    assert r2.persist.unchanged == r1.persist.inserted
    assert r2.persist.inserted == 0


# ============================================================================
# IndexMembers
# ============================================================================

def test_index_members_normalize_tags_index_name():
    data = _load("probe_index_nifty50.json")
    rows = IndexMembers().normalize(data, Request(
        path_or_url="x", meta={"index_name": "NIFTY 50"}
    ))
    assert all(r["index_name"] == "NIFTY 50" for r in rows)
    # No NIFTY 50 row itself
    assert "NIFTY 50" not in {r["symbol"] for r in rows}


def test_index_members_assigns_weightage_rank():
    data = _load("probe_index_nifty50.json")
    rows = IndexMembers().normalize(data, Request(
        path_or_url="x", meta={"index_name": "NIFTY 50"}
    ))
    ranks = sorted(r["weightage_rank"] for r in rows)
    # Ranks 1, 2, 3, ... up to len(rows)
    assert ranks == list(range(1, len(rows) + 1))


# ============================================================================
# NewListings — empty response is valid
# ============================================================================

def test_new_listings_handles_empty():
    """Probe today returned non-JSON (empty body). Empty must not crash."""
    assert NewListings().normalize(None, Request(path_or_url="x")) == []
    assert NewListings().normalize({}, Request(path_or_url="x")) == []
    assert NewListings().normalize([], Request(path_or_url="x")) == []


def test_new_listings_normalize_with_synthetic_row():
    """Verify normalize() with a synthetic listing (since live is empty)."""
    payload = [{
        "symbol": "NEWCO",
        "companyName": "New Company Limited",
        "series": "EQ",
        "listingDate": "20-May-2026",
        "isin": "INE000A01001",
        "issuePrice": "100",
        "marketLot": "10",
        "faceValue": "1",
    }]
    rows = NewListings().normalize(payload, Request(path_or_url="x"))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NEWCO"
    assert rows[0]["issue_price"] == 100.0


def test_new_listings_fingerprint_distinguishes_dates():
    c = NewListings()
    r1 = {"symbol": "NEWCO", "listing_date": "20-May-2026"}
    r2 = {"symbol": "NEWCO", "listing_date": "21-May-2026"}
    assert c.fingerprint(r1) != c.fingerprint(r2)


# ============================================================================
# PrimaryMarket
# ============================================================================

def test_primary_market_normalize_ipo():
    data = _load("probe_primary_ipo.json")
    rows = PrimaryMarket().normalize(
        data, Request(path_or_url="x", meta={"issue_type": "ipo"})
    )
    assert all(r["issue_type"] == "ipo" for r in rows)
    assert len(rows) > 0   # 2 IPOs in fixture


def test_primary_market_handles_empty_category():
    """OFS/rights/NCDs returned {} — must be empty rows, not crash."""
    rows = PrimaryMarket().normalize(
        {}, Request(path_or_url="x", meta={"issue_type": "ofs"})
    )
    assert rows == []


def test_primary_market_fingerprint_distinguishes_types():
    c = PrimaryMarket()
    r1 = {"issue_type": "ipo",    "symbol": "X", "company_name": "X Ltd", "open_date": "20-May-2026"}
    r2 = {"issue_type": "rights", "symbol": "X", "company_name": "X Ltd", "open_date": "20-May-2026"}
    assert c.fingerprint(r1) != c.fingerprint(r2)


# ============================================================================
# QuoteMetadata
# ============================================================================

def test_quote_metadata_normalize_extracts_nested_fields():
    data = _load("probe_quote_meta_reliance.json")
    rows = QuoteMetadata().normalize(
        data, Request(path_or_url="x", meta={"symbol": "RELIANCE"})
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "RELIANCE"
    assert row["industry"]   # Reliance is in some industry
    assert row["isin"]
    assert row["listing_date"]


def test_quote_metadata_is_fno_flag():
    """RELIANCE is F&O; should set is_fno=1."""
    data = _load("probe_quote_meta_reliance.json")
    rows = QuoteMetadata().normalize(
        data, Request(path_or_url="x", meta={"symbol": "RELIANCE"})
    )
    assert rows[0]["is_fno"] == 1


def test_quote_metadata_handles_malformed():
    rows = QuoteMetadata().normalize(None, Request(
        path_or_url="x", meta={"symbol": "X"}
    ))
    assert rows == []
    rows = QuoteMetadata().normalize({}, Request(
        path_or_url="x", meta={"symbol": "X"}
    ))
    # Empty dict still produces one row with mostly NULLs, but symbol set
    assert len(rows) == 1
    assert rows[0]["symbol"] == "X"