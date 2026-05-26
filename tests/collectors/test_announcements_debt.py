"""
Acceptance tests for the Debt announcements collector.

Debt announcements have no equity symbol (always null) and land in the shared
non-equity table (raw_nonequity_announcements, segment='debt'), metadata-only —
not the equity raw_announcements table or the Layer 3 PDF pipeline. Fixture is
a 3-row live capture (all symbol=null, distinct seq_ids).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.announcements import DebtAnnouncements
from nse_data.collectors.base import Request

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "announcements_debt.json"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"
ENDPOINT = "/api/corporate-announcements"


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
    return DebtAnnouncements().normalize(fixture_data, Request(path_or_url="x"))


# ============================================================================
# Unit — normalize()
# ============================================================================

def test_plan_requests_debt_index():
    assert DebtAnnouncements().plan()[0].params == {"index": "debt"}


def test_keeps_null_symbol_rows(fixture_data):
    """The equity collector would drop these (no symbol); debt must keep them."""
    rows = _rows(fixture_data)
    assert len(rows) == 3
    assert all(r["segment"] == "debt" for r in rows)
    assert all(r["symbol"] is None for r in rows)


def test_field_mapping(fixture_data):
    kotak = next(r for r in _rows(fixture_data)
                 if r["company_name"] == "Kotak Mahindra Bank Limited")
    assert kotak["seq_id"] == "227669"
    assert kotak["subject"] == "Updates"
    assert kotak["broadcast_dt"] == "26-MAY-2026 19:28:27"
    assert kotak["orgid"] == "469"
    assert kotak["attachment_url"].startswith("https://nsearchives.nseindia.com/content/debt/")
    assert kotak["details"].startswith("Kotak Mahindra Bank")


def test_fingerprint_is_segment_prefixed_content_tuple(fixture_data):
    c = DebtAnnouncements()
    row = _rows(fixture_data)[0]
    key = "|".join(["debt", row["seq_id"] or "", row["symbol"] or "",
                    row["company_name"] or "", row["subject"], row["broadcast_dt"],
                    row["attachment_url"] or ""])
    expected = hashlib.sha256(key.encode()).hexdigest()[:16]
    assert c.fingerprint(row) == expected
    assert len(c.fingerprint(row)) == 16


def test_distinct_fingerprints_for_distinct_filings(fixture_data):
    fps = {DebtAnnouncements().fingerprint(r) for r in _rows(fixture_data)}
    assert len(fps) == 3


def test_handles_empty_and_malformed():
    c = DebtAnnouncements()
    assert c.normalize([], Request(path_or_url="x")) == []
    assert c.normalize({}, Request(path_or_url="x")) == []
    assert c.normalize(None, Request(path_or_url="x")) == []


# ============================================================================
# Integration — insert + dedup into raw_nonequity_announcements
# ============================================================================

def test_run_inserts_into_nonequity_table(session, db):
    report = DebtAnnouncements().run(session, db)
    assert report.rows_seen == 3
    assert report.persist.inserted == 3
    n = db.execute(
        "SELECT COUNT(*) FROM raw_nonequity_announcements WHERE segment='debt'"
    ).fetchone()[0]
    assert n == 3
    # Did NOT touch the equity announcements table.
    eq = db.execute("SELECT COUNT(*) FROM raw_announcements").fetchone()[0]
    assert eq == 0


def test_rerun_dedups(session, db):
    DebtAnnouncements().run(session, db)
    r2 = DebtAnnouncements().run(session, db)
    assert r2.persist.inserted == 0
    assert r2.persist.deduped == 3


def test_null_symbol_persists_ok(session, db):
    """Sanity: the row writes despite having no symbol."""
    DebtAnnouncements().run(session, db)
    company, symbol = db.execute(
        "SELECT company_name, symbol FROM raw_nonequity_announcements WHERE seq_id='227669'"
    ).fetchone()
    assert company == "Kotak Mahindra Bank Limited"
    assert symbol is None
