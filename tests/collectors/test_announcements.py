"""
Phase 2 acceptance tests for the Announcements collector.

Mirrors the phase exit checklist:
  - normalize() unit test against captured fixture
  - idempotency: re-run produces no new rows
  - mutation: one field changed -> exactly one new row
  - cache hot-path: rows known to cache skip SQLite entirely
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.announcements import Announcements
from nse_data.collectors.base import Request
from nse_data.storage.cache import MemoryDedupCache

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "announcements_equity.json"
MIGRATION_PATH = Path(__file__).parent.parent.parent / "migrations" / "001_initial.sql"


# ----- DB fixture: real schema, not the toy one from conftest -----

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(MIGRATION_PATH.read_text())
    yield conn
    conn.close()


@pytest.fixture
def fixture_data():
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def session(fixture_data):
    # The collector's plan() sends params={"index": "equities"}; FakeSession
    # routes off params["__fixture"] if present, else the URL. To make this
    # work without setting __fixture in the real collector, we use the URL.
    return FakeSession(json_fixtures={"/api/corporate-announcements": fixture_data})


# ============================================================================
# Unit — normalize()
# ============================================================================

def test_normalize_produces_expected_row_count(fixture_data):
    """The malformed 4th entry should be dropped."""
    collector = Announcements()
    req = Request(path_or_url="/api/corporate-announcements")
    rows = collector.normalize(fixture_data, req)
    assert len(rows) == 3
    assert {r["symbol"] for r in rows} == {"RELIANCE", "TCS", "INFY"}


def test_normalize_fills_layer3_fields_with_null(fixture_data):
    """priority/pdf_path/extracted/sentiment stay NULL — Layer 3 fills them."""
    rows = Announcements().normalize(fixture_data, Request(path_or_url="x"))
    for row in rows:
        assert row["priority"] is None
        assert row["pdf_path"] is None
        assert row["pdf_text"] is None
        assert row["extracted"] is None
        assert row["sentiment"] is None
        assert row["pdf_status"] == "pending"


def test_normalize_makes_pdf_urls_absolute(fixture_data):
    rows = Announcements().normalize(fixture_data, Request(path_or_url="x"))
    for row in rows:
        url = row["attachment_url"]
        assert url is not None
        assert url.startswith("https://www.nseindia.com/")


def test_fingerprint_is_stable(fixture_data):
    """Same input -> same fingerprint, every time. This is the dedup contract."""
    rows = Announcements().normalize(fixture_data, Request(path_or_url="x"))
    c = Announcements()
    fps_1 = [c.fingerprint(r) for r in rows]
    fps_2 = [c.fingerprint(r) for r in rows]
    assert fps_1 == fps_2
    # Different rows must produce different fingerprints
    assert len(set(fps_1)) == len(fps_1)


def test_fingerprint_changes_when_subject_changes(fixture_data):
    c = Announcements()
    row = c.normalize(fixture_data, Request(path_or_url="x"))[0]
    fp1 = c.fingerprint(row)
    row["subject"] = row["subject"] + " (corrected)"
    fp2 = c.fingerprint(row)
    assert fp1 != fp2


def test_normalize_handles_dict_wrapped_response(fixture_data):
    """NSE sometimes returns {data: [...]} instead of a bare list."""
    wrapped = {"data": fixture_data}
    rows = Announcements().normalize(wrapped, Request(path_or_url="x"))
    assert len(rows) == 3


def test_normalize_handles_empty_response():
    assert Announcements().normalize([], Request(path_or_url="x")) == []
    assert Announcements().normalize({}, Request(path_or_url="x")) == []
    assert Announcements().normalize(None, Request(path_or_url="x")) == []


# ============================================================================
# Integration — idempotent re-run
# ============================================================================

def test_run_inserts_all_rows_first_time(session, db):
    report = Announcements().run(session, db)
    assert report.fetched == 1 and report.succeeded == 1
    assert report.rows_seen == 3
    assert report.persist.inserted == 3
    assert report.persist.deduped == 0

    count = db.execute("SELECT COUNT(*) FROM raw_announcements").fetchone()[0]
    assert count == 3


def test_rerun_dedups_all_rows(session, db):
    """Phase 2 spec: 'second run reports inserted=0, deduped=N'."""
    Announcements().run(session, db)
    report = Announcements().run(session, db)

    assert report.persist.inserted == 0
    assert report.persist.deduped == 3

    count = db.execute("SELECT COUNT(*) FROM raw_announcements").fetchone()[0]
    assert count == 3  # no duplicates


def test_mutate_one_field_yields_exactly_one_new_row(fixture_data, db):
    """Phase 2 spec: 'mutate one field -> exactly one new row appears'."""
    s1 = FakeSession(json_fixtures={"/api/corporate-announcements": fixture_data})
    Announcements().run(s1, db)

    # Mutate the subject of one entry — this changes its fingerprint
    mutated = json.loads(json.dumps(fixture_data))  # deep copy
    mutated[0]["desc"] = mutated[0]["desc"] + " - REVISED"
    s2 = FakeSession(json_fixtures={"/api/corporate-announcements": mutated})

    report = Announcements().run(s2, db)
    assert report.persist.inserted == 1
    assert report.persist.deduped == 2

    count = db.execute("SELECT COUNT(*) FROM raw_announcements").fetchone()[0]
    assert count == 4


# ============================================================================
# Integration — Redis hot-set + SQLite fallback
# ============================================================================

def test_cache_hits_skip_sqlite_entirely(session, db):
    """
    If the cache already knows a fingerprint, persist() should not insert
    into SQLite for that row. This is the hot-set's whole purpose.
    """
    cache = MemoryDedupCache()

    # Pre-seed cache with one row's fingerprint
    collector = Announcements()
    rows = collector.normalize(json.loads(FIXTURE_PATH.read_text()),
                               Request(path_or_url="x"))
    seeded_fp = collector.fingerprint(rows[0])
    cache.add_many([seeded_fp])

    collector.dedup_cache = cache
    report = collector.run(session, db)

    # 3 rows seen, 1 hit cache (skipped), 2 went to SQLite (inserted fresh)
    assert report.persist.inserted == 2
    assert report.persist.deduped == 1

    # And the cached row is NOT in SQLite — it was skipped before SQL ran
    in_db = db.execute(
        "SELECT COUNT(*) FROM raw_announcements WHERE fingerprint=?",
        (seeded_fp,),
    ).fetchone()[0]
    assert in_db == 0


def test_cache_populated_after_run(session, db):
    """All rows that pass through SQLite get added to the cache."""
    cache = MemoryDedupCache()
    collector = Announcements()
    collector.dedup_cache = cache
    collector.run(session, db)

    # All 3 normalized rows should now be in the cache
    assert len(cache) == 3


def test_cache_disabled_path_still_works(session, db):
    """No cache set -> behaves exactly like Phase 1 EventCollector."""
    collector = Announcements()
    assert collector.dedup_cache is None
    report = collector.run(session, db)
    assert report.persist.inserted == 3