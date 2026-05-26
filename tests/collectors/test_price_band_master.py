"""
Acceptance tests for the PriceBandMaster collector (daily sec_list.csv).

ReferenceCollector reading the fixed-URL price-band CSV into raw_price_bands.
Fixture is a 10-row trim of a live sec_list.csv covering bands 2/5/10/20/No Band
and series EQ/BE/BZ/ST/SM/IT, including a GSM-remark row.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.price_band_master import PriceBandMaster, SEC_LIST_URL

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "sec_list.csv"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


@pytest.fixture
def csv_text():
    return FIXTURE_PATH.read_text()


@pytest.fixture
def session(csv_text):
    return FakeSession(text_fixtures={SEC_LIST_URL: csv_text})


def _rows(csv_text):
    return PriceBandMaster().normalize(csv_text, Request(path_or_url="x"))


# ============================================================================
# Unit — normalize() / CSV parsing
# ============================================================================

def test_plan_targets_fixed_csv_url():
    req = PriceBandMaster().plan()[0]
    assert req.path_or_url == SEC_LIST_URL
    assert req.response_type == "text"


def test_parses_all_rows(csv_text):
    rows = _rows(csv_text)
    assert len(rows) == 10
    assert all(r["symbol"] and r["series"] for r in rows)


def test_band_parsed_to_int(csv_text):
    rows = {(r["symbol"], r["series"]): r for r in _rows(csv_text)}
    assert rows[("21STCENMGM", "EQ")]["band"] == 2
    assert rows[("A2ZINFRA", "EQ")]["band"] == 5
    assert rows[("20MICRONS", "EQ")]["band"] == 20
    assert isinstance(rows[("21STCENMGM", "EQ")]["band"], int)


def test_no_band_becomes_none(csv_text):
    row = next(r for r in _rows(csv_text) if r["symbol"] == "360ONE")
    assert row["band"] is None


def test_remarks_dash_to_none_and_gsm_kept(csv_text):
    rows = {r["symbol"]: r for r in _rows(csv_text)}
    assert rows["21STCENMGM"]["remarks"] is None          # was '-'
    assert rows["ANSALAPI"]["remarks"] == "GSM STAGE - II"
    assert rows["ANSALAPI"]["series"] == "BZ"             # restricted/T2T series


def test_t2t_series_present(csv_text):
    """T2T/restricted segment is a Series filter over this table."""
    series = {r["series"] for r in _rows(csv_text)}
    assert {"BE", "BZ", "ST"} <= series


def test_empty_and_malformed():
    c = PriceBandMaster()
    assert c.normalize("", Request(path_or_url="x")) == []
    assert c.normalize(None, Request(path_or_url="x")) == []
    assert c.normalize("Symbol,Series,Security Name,Band,Remarks\n", Request(path_or_url="x")) == []


# ============================================================================
# Integration — diff semantics
# ============================================================================

def test_run_inserts_all(session, db):
    report = PriceBandMaster().run(session, db)
    assert report.persist.inserted == 10
    n = db.execute("SELECT COUNT(*) FROM raw_price_bands").fetchone()[0]
    assert n == 10


def test_band_tightening_is_update(session, db, csv_text):
    PriceBandMaster().run(session, db)
    # 20MICRONS tightens from band 20 -> 2 next day.
    tightened = csv_text.replace(
        "20MICRONS,EQ,20 MICRONS LIMITED,20,-",
        "20MICRONS,EQ,20 MICRONS LIMITED,2,GSM STAGE - I",
    )
    assert tightened != csv_text   # guard: the replace actually matched
    report = PriceBandMaster().run(
        FakeSession(text_fixtures={SEC_LIST_URL: tightened}), db
    )
    assert report.persist.updated == 1
    band = db.execute(
        "SELECT band FROM raw_price_bands WHERE symbol='20MICRONS' AND series='EQ'"
    ).fetchone()[0]
    assert band == 2


def test_composite_key_allows_same_symbol_two_series(db):
    """A symbol can list under two series (e.g. ELECTCAST EQ + W1)."""
    csv_text = (
        "Symbol,Series,Security Name,Band,Remarks\n"
        "ELECTCAST,EQ,ELECTROSTEEL CASTINGS LIMITED,20,-\n"
        "ELECTCAST,W1,ELECTROSTEEL CASTINGS LIMITED,20,-\n"
    )
    report = PriceBandMaster().run(FakeSession(text_fixtures={SEC_LIST_URL: csv_text}), db)
    assert report.persist.inserted == 2
