"""Phase 7 Day 3 acceptance tests — 5 EventCollectors."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.board_meetings import BoardMeetings
from nse_data.collectors.corporate_actions import CorporateActions
from nse_data.collectors.financial_results import FinancialResults
from nse_data.collectors.insider_trading import InsiderTrading
from nse_data.collectors.large_deals import LargeDeals

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
# BoardMeetings
# ============================================================================

def test_board_meetings_normalize_extracts_fields():
    data = _load("board_meetings.json")
    rows = BoardMeetings().normalize(data, Request(path_or_url="x"))
    assert len(rows) > 0
    first = rows[0]
    assert first["symbol"]
    assert first["meeting_date"]
    assert first["purpose"]


def test_board_meetings_fingerprint_is_stable():
    c = BoardMeetings()
    row = {
        "symbol": "RELIANCE", "meeting_date": "27-May-2026",
        "purpose": "Board Meeting Intimation",
    }
    assert c.fingerprint(row) == c.fingerprint(row)


def test_board_meetings_dedups_on_rerun(db):
    data = _load("board_meetings.json")
    session = FakeSession(json_fixtures={"/api/corporate-board-meetings": data})
    r1 = BoardMeetings().run(session, db)
    r2 = BoardMeetings().run(session, db)
    assert r1.persist.inserted > 0
    assert r2.persist.inserted == 0
    assert r2.persist.deduped == r1.persist.inserted


# ============================================================================
# CorporateActions
# ============================================================================

def test_corporate_actions_handles_dash_dates():
    """NSE writes '-' for missing dates; must become NULL."""
    data = _load("corporate_actions.json")
    rows = CorporateActions().normalize(data, Request(path_or_url="x"))
    # Most rows have '-' in some date fields
    nulls = sum(1 for r in rows if r["bc_start_date"] is None)
    assert nulls > 0


def test_corporate_actions_persists_with_face_value(db):
    data = _load("corporate_actions.json")
    session = FakeSession(json_fixtures={"/api/corporates-corporateActions": data})
    report = CorporateActions().run(session, db)
    assert report.succeeded == 1
    assert report.persist.inserted > 0

    fv_count = db.execute(
        "SELECT COUNT(*) FROM raw_corporate_actions WHERE face_value IS NOT NULL"
    ).fetchone()[0]
    assert fv_count > 0


# ============================================================================
# FinancialResults — large payload
# ============================================================================

def test_financial_results_normalize_handles_archive_size():
    """NSE returns ~3,800 rows; must parse without crashing."""
    data = _load("financial_results.json")
    rows = FinancialResults().normalize(data, Request(path_or_url="x"))
    assert len(rows) > 1000   # Large archive


def test_financial_results_fingerprint_distinguishes_quarters():
    c = FinancialResults()
    row1 = {
        "symbol": "RELIANCE", "period": "Quarterly",
        "relating_to": "Third Quarter", "filing_date": "20-Feb-2026",
    }
    row2 = {**row1, "relating_to": "Fourth Quarter"}
    assert c.fingerprint(row1) != c.fingerprint(row2)


def test_financial_results_dedups_full_archive(db):
    data = _load("financial_results.json")
    session = FakeSession(json_fixtures={"/api/corporates-financial-results": data})
    r1 = FinancialResults().run(session, db)
    r2 = FinancialResults().run(session, db)

    # First run: insert + dedup accounts for every row in the payload.
    # Some payloads have internal duplicates (same fingerprint twice).
    assert r1.persist.inserted + r1.persist.deduped == r1.rows_seen
    assert r1.persist.inserted > 0

    # Second run: zero new inserts; every row arriving is already known.
    assert r2.persist.inserted == 0
    assert r2.persist.deduped == r2.rows_seen
# ============================================================================
# LargeDeals — three deal_types in one response
# ============================================================================

def test_large_deals_walks_all_blocks():
    data = _load("large_deals_live.json")
    rows = LargeDeals().normalize(data, Request(path_or_url="x"))
    deal_types = {r["deal_type"] for r in rows}
    # Bulk should definitely be present; block + short may or may not depending on day
    assert "bulk" in deal_types


def test_large_deals_persists_and_tags_correctly(db):
    data = _load("large_deals_live.json")
    session = FakeSession(json_fixtures={"/api/snapshot-capital-market-largedeal": data})
    report = LargeDeals().run(session, db)
    assert report.succeeded == 1
    assert report.persist.inserted > 0

    bulk_count = db.execute(
        "SELECT COUNT(*) FROM raw_large_deals WHERE deal_type='bulk'"
    ).fetchone()[0]
    assert bulk_count > 0


def test_large_deals_fingerprint_distinguishes_clients():
    c = LargeDeals()
    row1 = {
        "deal_type": "bulk", "deal_date": "19-May-2026", "symbol": "RELIANCE",
        "client_name": "FUND A", "buy_sell": "BUY", "quantity": 100000,
    }
    row2 = {**row1, "client_name": "FUND B"}
    assert c.fingerprint(row1) != c.fingerprint(row2)


# ============================================================================
# InsiderTrading — empty response is valid
# ============================================================================

def test_insider_trading_handles_empty_payload(db):
    """Probe returned 0 rows. Empty must not be an error."""
    session = FakeSession(json_fixtures={
        "/api/corporates-pit": {"data": [], "acqNameList": []}
    })
    report = InsiderTrading().run(session, db)
    assert report.succeeded == 1
    assert report.rows_seen == 0
    assert report.persist.inserted == 0
    assert report.failed == 0


def test_insider_trading_normalize_with_synthetic_row():
    """Since live data is empty, verify normalize() works on a synthetic row."""
    payload = {"data": [{
        "symbol": "RELIANCE",
        "company": "Reliance Industries Limited",
        "acqName": "Mukesh D Ambani",
        "personCategory": "Promoter",
        "tdpTransactionType": "Buy",
        "secAcq": "10000",
        "secVal": "29000000",
        "tdpDate": "19-May-2026",
    }]}
    rows = InsiderTrading().normalize(payload, Request(path_or_url="x"))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "RELIANCE"
    assert rows[0]["acquirer_name"] == "Mukesh D Ambani"
    assert rows[0]["no_of_securities"] == 10000