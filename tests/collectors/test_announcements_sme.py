"""
Acceptance tests for the SME announcements collector.

SmeAnnouncements is Announcements with index=sme / segment='sme'. The feed
shape is identical to equities, so these tests focus on what's specific to the
subclass: the index param, the segment stamp, and that the inherited
fingerprint/dedup still hold. Fixture is a 3-row live capture.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.announcements import Announcements, SmeAnnouncements
from nse_data.collectors.base import Request
from nse_data.storage.cache import MemoryDedupCache

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "announcements_sme.json"
MIGRATION_PATH = Path(__file__).parent.parent.parent / "migrations" / "001_initial.sql"
ENDPOINT = "/api/corporate-announcements"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(MIGRATION_PATH.read_text())
    conn.executescript((MIGRATION_PATH.parent / "076_announcement_broadcast_epoch.sql").read_text())
    yield conn
    conn.close()


@pytest.fixture
def fixture_data():
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def session(fixture_data):
    return FakeSession(json_fixtures={ENDPOINT: fixture_data})


# ============================================================================
# Subclass wiring
# ============================================================================

def test_plan_requests_sme_index():
    reqs = SmeAnnouncements().plan()
    assert len(reqs) == 1
    assert reqs[0].path_or_url == ENDPOINT
    assert reqs[0].params == {"index": "sme"}


def test_rows_stamped_with_sme_segment(fixture_data):
    rows = SmeAnnouncements().normalize(fixture_data, Request(path_or_url="x"))
    assert len(rows) == 3
    assert {r["symbol"] for r in rows} == {"ABHAPOWER", "DECCANTRAN", "BOSS"}
    assert all(r["segment"] == "sme" for r in rows)


def test_equity_base_still_stamps_equities(fixture_data):
    """The parameterization must not change the base equity collector."""
    rows = Announcements().normalize(fixture_data, Request(path_or_url="x"))
    assert all(r["segment"] == "equities" for r in rows)


def test_layer3_fields_null_and_pending(fixture_data):
    rows = SmeAnnouncements().normalize(fixture_data, Request(path_or_url="x"))
    for r in rows:
        assert r["priority"] is None
        assert r["pdf_status"] == "pending"
        assert r["attachment_url"].startswith("https://nsearchives.nseindia.com/")


def test_fingerprint_stable_and_segment_independent(fixture_data):
    """SME symbols are exchange-unique, so the symbol|subject|broadcast_dt
    fingerprint matches the base formula (no segment folded in)."""
    sme = SmeAnnouncements()
    rows = sme.normalize(fixture_data, Request(path_or_url="x"))
    row = rows[0]
    fp = sme.fingerprint(row)
    # Same formula as the base class.
    assert fp == Announcements().fingerprint(row)
    assert len(fp) == 16


# ============================================================================
# Integration — insert + idempotency
# ============================================================================

def test_run_inserts_all_rows(session, db):
    report = SmeAnnouncements().run(session, db)
    assert report.rows_seen == 3
    assert report.persist.inserted == 3
    n = db.execute(
        "SELECT COUNT(*) FROM raw_announcements WHERE segment='sme'"
    ).fetchone()[0]
    assert n == 3


def test_rerun_dedups(session, db):
    SmeAnnouncements().run(session, db)
    r2 = SmeAnnouncements().run(session, db)
    assert r2.persist.inserted == 0
    assert r2.persist.deduped == 3


def test_cache_hot_path_skips_sqlite(fixture_data, db):
    cache = MemoryDedupCache()
    c1 = SmeAnnouncements()
    c1.dedup_cache = cache
    c1.run(FakeSession(json_fixtures={ENDPOINT: fixture_data}), db)
    assert len(cache) == 3

    c2 = SmeAnnouncements()
    c2.dedup_cache = cache
    r2 = c2.run(FakeSession(json_fixtures={ENDPOINT: fixture_data}), db)
    assert r2.persist.deduped == 3
    assert r2.persist.inserted == 0
