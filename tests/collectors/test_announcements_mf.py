"""
Acceptance tests for the MF (mutual fund + ETF) announcements collector.

Shares raw_nonequity_announcements with debt (segment='mf'). The fixture is a
3-row live capture exercising two MF-specific quirks:
  - an ETF row carries a real `symbol` (ITBEES) — symbol is NOT always null here
  - two rows reuse one seq_id (106622094): the same disclosure tagged to the ETF
    and untagged. They must stay distinct (the content-tuple fingerprint, not
    seq_id alone, is why).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.announcements import MfAnnouncements
from nse_data.collectors.base import Request

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "announcements_mf.json"
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
    return MfAnnouncements().normalize(fixture_data, Request(path_or_url="x"))


# ============================================================================
# Unit
# ============================================================================

def test_plan_requests_mf_index():
    assert MfAnnouncements().plan()[0].params == {"index": "mf"}


def test_all_rows_tagged_mf(fixture_data):
    rows = _rows(fixture_data)
    assert len(rows) == 3
    assert all(r["segment"] == "mf" for r in rows)


def test_etf_symbol_preserved(fixture_data):
    """mf rows can carry a symbol (ETFs) — it must be kept, not dropped."""
    rows = _rows(fixture_data)
    itbees = [r for r in rows if r["symbol"] == "ITBEES"]
    assert len(itbees) == 1
    # And the untagged variant has no symbol.
    assert any(r["symbol"] is None for r in rows)


def test_duplicate_seq_id_rows_stay_distinct(fixture_data):
    """Two rows share seq_id 106622094 (ETF-tagged + untagged variant of the
    same disclosure). The content-tuple fingerprint keeps them distinct."""
    c = MfAnnouncements()
    dupes = [r for r in _rows(fixture_data) if r["seq_id"] == "106622094"]
    assert len(dupes) == 2
    fps = {c.fingerprint(r) for r in dupes}
    assert len(fps) == 2   # distinct despite shared seq_id


def test_all_three_fingerprints_distinct(fixture_data):
    fps = {MfAnnouncements().fingerprint(r) for r in _rows(fixture_data)}
    assert len(fps) == 3


# ============================================================================
# Integration
# ============================================================================

def test_run_inserts_and_coexists_with_debt(session, db):
    report = MfAnnouncements().run(session, db)
    assert report.persist.inserted == 3
    n = db.execute(
        "SELECT COUNT(*) FROM raw_nonequity_announcements WHERE segment='mf'"
    ).fetchone()[0]
    assert n == 3


def test_rerun_dedups(session, db):
    MfAnnouncements().run(session, db)
    r2 = MfAnnouncements().run(session, db)
    assert r2.persist.inserted == 0
    assert r2.persist.deduped == 3


def test_debt_and_mf_share_table_without_collision(db, fixture_data):
    """Debt and MF write to the same table, partitioned by segment."""
    from nse_data.collectors.announcements import DebtAnnouncements
    debt_fix = json.loads(
        (FIXTURE_PATH.parent / "announcements_debt.json").read_text()
    )
    DebtAnnouncements().run(FakeSession(json_fixtures={ENDPOINT: debt_fix}), db)
    MfAnnouncements().run(FakeSession(json_fixtures={ENDPOINT: fixture_data}), db)

    total = db.execute("SELECT COUNT(*) FROM raw_nonequity_announcements").fetchone()[0]
    by_seg = dict(db.execute(
        "SELECT segment, COUNT(*) FROM raw_nonequity_announcements GROUP BY segment"
    ).fetchall())
    assert total == 6
    assert by_seg == {"debt": 3, "mf": 3}
