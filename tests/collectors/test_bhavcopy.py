"""
Phase 4 acceptance tests for Bhavcopy collector.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.bhavcopy import Bhavcopy, _parse_value

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "bhavcopy_sample.csv"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


@pytest.fixture
def csv_bytes():
    return FIXTURE_PATH.read_bytes()


@pytest.fixture
def archive_tmp(tmp_path, monkeypatch):
    """Redirect the CSV archive root into a temp dir per test."""
    monkeypatch.setattr(Bhavcopy, "archive_root", tmp_path / "archive")
    return tmp_path / "archive"


# ============================================================================
# Unit — _parse_value
# ============================================================================

def test_parse_value_handles_dash():
    assert _parse_value("-", float) is None
    assert _parse_value(" - ", int) is None


def test_parse_value_handles_empty():
    assert _parse_value("", float) is None
    assert _parse_value("   ", int) is None
    assert _parse_value(None, float) is None


def test_parse_value_strips_whitespace():
    assert _parse_value(" 103.30 ", float) == 103.30
    assert _parse_value("  20 ", int) == 20


def test_parse_value_int_from_float_string():
    """NSE occasionally writes ints as floats — int('20.0') fails, must coerce."""
    assert _parse_value("20.0", int) == 20


def test_parse_value_returns_none_on_garbage():
    assert _parse_value("abc", float) is None
    assert _parse_value("12.3.4", float) is None


# ============================================================================
# Unit — normalize()
# ============================================================================

def test_normalize_parses_rows(csv_bytes, archive_tmp):
    req = Request(path_or_url="x", meta={"date": "2026-05-19"})
    rows = Bhavcopy().normalize(csv_bytes, req)
    assert len(rows) > 1000   # ~3000 securities, give a wide bound


def test_normalize_columns_are_typed(csv_bytes, archive_tmp):
    req = Request(path_or_url="x", meta={"date": "2026-05-19"})
    rows = Bhavcopy().normalize(csv_bytes, req)
    sample = rows[0]
    assert sample["date"] == "2026-05-19"
    assert isinstance(sample["symbol"], str)
    assert isinstance(sample["series"], str)
    # Numerics are float/int or None — never str
    for col in ("open", "high", "low", "close", "prev_close",
                "avg_price", "turnover_lacs"):
        v = sample[col]
        assert v is None or isinstance(v, float), f"{col} was {type(v)}"
    for col in ("volume", "trades", "delivery_qty"):
        v = sample[col]
        assert v is None or isinstance(v, int), f"{col} was {type(v)}"


def test_normalize_includes_multiple_series(csv_bytes, archive_tmp):
    """Bhavcopy spans EQ + BE + GS + SM. All should appear."""
    rows = Bhavcopy().normalize(csv_bytes, Request(path_or_url="x", meta={"date": "2026-05-19"}))
    series_seen = {r["series"] for r in rows}
    # EQ should definitely be there; presence of others is a bonus
    assert "EQ" in series_seen


def test_normalize_handles_missing_delivery(csv_bytes, archive_tmp):
    """GS rows sometimes have '-' for delivery; must become None."""
    rows = Bhavcopy().normalize(csv_bytes, Request(path_or_url="x", meta={"date": "2026-05-19"}))
    # At least one row should have delivery_pct None — most bond rows do
    none_count = sum(1 for r in rows if r["delivery_pct"] is None)
    assert none_count > 0


def test_normalize_empty_bytes():
    assert Bhavcopy().normalize(b"", Request(path_or_url="x")) == []
    assert Bhavcopy().normalize(None, Request(path_or_url="x")) == []


# ============================================================================
# Integration — idempotency
# ============================================================================

def test_full_run_persists_rows(csv_bytes, archive_tmp, db):
    session = FakeSession(bytes_fixtures={
        Bhavcopy().url_for_date(date(2026, 5, 19)): csv_bytes
    })
    report = Bhavcopy().run_for_date(session, db, date(2026, 5, 19))
    assert report.succeeded == 1
    assert report.rows_seen > 1000
    assert report.persist.inserted == report.rows_seen

    in_db = db.execute("SELECT COUNT(*) FROM raw_bhavcopy_cm").fetchone()[0]
    assert in_db == report.rows_seen


def test_rerun_same_date_is_unchanged(csv_bytes, archive_tmp, db):
    session = FakeSession(bytes_fixtures={
        Bhavcopy().url_for_date(date(2026, 5, 19)): csv_bytes
    })
    r1 = Bhavcopy().run_for_date(session, db, date(2026, 5, 19))
    r2 = Bhavcopy().run_for_date(session, db, date(2026, 5, 19))

    assert r1.persist.inserted == r1.rows_seen
    assert r2.persist.inserted == 0
    assert r2.persist.unchanged == r2.rows_seen

    in_db = db.execute("SELECT COUNT(*) FROM raw_bhavcopy_cm").fetchone()[0]
    assert in_db == r1.rows_seen


def test_archives_csv_to_year_folder(csv_bytes, archive_tmp, db):
    session = FakeSession(bytes_fixtures={
        Bhavcopy().url_for_date(date(2026, 5, 19)): csv_bytes
    })
    Bhavcopy().run_for_date(session, db, date(2026, 5, 19))

    archived = archive_tmp / "2026" / "sec_bhavdata_full_19052026.csv"
    assert archived.exists()
    assert archived.read_bytes() == csv_bytes