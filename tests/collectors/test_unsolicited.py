"""
Acceptance tests for the UnsolicitedWatchlist collector (XLSX → blacklist).

The live list is usually empty; the fixture is a constructed XLSX mirroring the
real sheet layout (title / blank / header / data rows / footer note) with two
sample securities. Verifies parsing, the empty-list case, diff semantics, and
that rows surface in the blacklist view.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import openpyxl
import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.unsolicited import UnsolicitedWatchlist, CURRENT_LIST_URL

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "unsolicited_watchlist.xlsx"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


@pytest.fixture
def xlsx_bytes():
    return FIXTURE_PATH.read_bytes()


@pytest.fixture
def session(xlsx_bytes):
    return FakeSession(bytes_fixtures={CURRENT_LIST_URL: xlsx_bytes})


def _empty_watchlist_xlsx() -> bytes:
    """Title + header + footer note, no data rows (the common live state)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Current"
    ws.append(["List of securities forming part of Current Watchlist"])
    ws.append([])
    ws.append(["Sr. No.", "Date of Dissemination", "Symbol", "Scrip Code",
               "Name of the Company", "Remarks", "Company Response"])
    ws.append([])
    ws.append(["Note: purely market surveillance."])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ============================================================================
# normalize()
# ============================================================================

def test_parses_data_rows_skipping_chrome(xlsx_bytes):
    rows = UnsolicitedWatchlist().normalize(xlsx_bytes, Request(path_or_url="x"))
    assert {r["symbol"] for r in rows} == {"PUMPCO", "TIPSTOCK"}


def test_field_mapping(xlsx_bytes):
    rows = {r["symbol"]: r for r in
            UnsolicitedWatchlist().normalize(xlsx_bytes, Request(path_or_url="x"))}
    pump = rows["PUMPCO"]
    assert pump["scrip_code"] == "543210"
    assert pump["company_name"] == "Pump Co Limited"
    assert pump["date_disseminated"] == "2026-05-20"
    assert pump["remarks"] == "Unsolicited SMS"
    assert pump["company_response"] == "Denied"
    assert rows["TIPSTOCK"]["company_response"] is None   # blank cell


def test_empty_watchlist_returns_no_rows():
    rows = UnsolicitedWatchlist().normalize(
        _empty_watchlist_xlsx(), Request(path_or_url="x")
    )
    assert rows == []


def test_non_bytes_and_garbage_inputs():
    c = UnsolicitedWatchlist()
    assert c.normalize(None, Request(path_or_url="x")) == []
    assert c.normalize(b"", Request(path_or_url="x")) == []
    assert c.normalize(b"not a zip", Request(path_or_url="x")) == []


# ============================================================================
# Integration — table + blacklist view + diff
# ============================================================================

def test_run_inserts_and_appears_in_blacklist(session, db):
    report = UnsolicitedWatchlist().run(session, db)
    assert report.persist.inserted == 2
    assert db.execute("SELECT COUNT(*) FROM raw_unsolicited_watchlist").fetchone()[0] == 2

    # The blacklist view (extended in migration 019) now surfaces them.
    bl = db.execute(
        "SELECT symbol, reason FROM blacklist WHERE feed='UNSOLICITED' ORDER BY symbol"
    ).fetchall()
    assert bl == [("PUMPCO", "Unsolicited SMS"), ("TIPSTOCK", "Unsolicited messages")]


def test_empty_watchlist_clears_table(session, db):
    UnsolicitedWatchlist().run(session, db)
    # Next day the watchlist is cleared.
    empty = FakeSession(bytes_fixtures={CURRENT_LIST_URL: _empty_watchlist_xlsx()})
    report = UnsolicitedWatchlist().run(empty, db)
    assert report.persist.removed == 2
    assert db.execute("SELECT COUNT(*) FROM raw_unsolicited_watchlist").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM blacklist WHERE feed='UNSOLICITED'").fetchone()[0] == 0


def test_symbol_leaving_is_removed(session, db, xlsx_bytes):
    UnsolicitedWatchlist().run(session, db)
    # PUMPCO drops off; only TIPSTOCK remains.
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb.active
    ws.delete_rows(4)   # the PUMPCO data row
    buf = io.BytesIO(); wb.save(buf)
    report = UnsolicitedWatchlist().run(
        FakeSession(bytes_fixtures={CURRENT_LIST_URL: buf.getvalue()}), db
    )
    assert report.persist.removed == 1
    remaining = {r[0] for r in db.execute("SELECT symbol FROM raw_unsolicited_watchlist")}
    assert remaining == {"TIPSTOCK"}
