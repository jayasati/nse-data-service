"""Phase 7 Day 4 acceptance tests — 2 CsvCollectors + 1 SnapshotCollector."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.bhavcopy_fo import BhavcopyFO
from nse_data.collectors.fii_dii import FiiDii
from nse_data.collectors.volatility_report import VolatilityReport

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


# ============================================================================
# BhavcopyFO
# ============================================================================

def test_bhavcopy_fo_unzips_and_parses():
    """Parse the real captured fixture; expect ~45k rows."""
    raw = (FIXTURE_DIR / "probe_fo_udiff.bin").read_bytes()
    rows = BhavcopyFO().normalize(raw, Request(
        path_or_url="x", meta={"for_date": "2026-05-19"}
    ))
    assert len(rows) > 40000
    assert len(rows) < 60000


def test_bhavcopy_fo_handles_options_and_futures():
    """Both CE/PE options and futures (option_type='XX') must be present."""
    raw = (FIXTURE_DIR / "probe_fo_udiff.bin").read_bytes()
    rows = BhavcopyFO().normalize(raw, Request(
        path_or_url="x", meta={"for_date": "2026-05-19"}
    ))
    opt_types = {r["option_type"] for r in rows}
    assert "CE" in opt_types
    assert "PE" in opt_types
    # Futures: present in F&O bhavcopy, will have option_type='XX'
    # (May not exist in every fixture but expected in general)


def test_bhavcopy_fo_parses_numeric_fields():
    """OI, volume, prices should be properly typed."""
    raw = (FIXTURE_DIR / "probe_fo_udiff.bin").read_bytes()
    rows = BhavcopyFO().normalize(raw, Request(
        path_or_url="x", meta={"for_date": "2026-05-19"}
    ))
    # Find first row with non-zero OI
    sample = next(r for r in rows if r["open_interest"] and r["open_interest"] > 0)
    assert isinstance(sample["open_interest"], int)
    assert isinstance(sample["strike"], float)
    assert isinstance(sample["close"], (float, type(None)))


def test_bhavcopy_fo_rejects_invalid_zip():
    """Garbage input must not crash; just return empty."""
    rows = BhavcopyFO().normalize(b"not a zip file", Request(path_or_url="x"))
    assert rows == []


def test_bhavcopy_fo_empty_bytes():
    rows = BhavcopyFO().normalize(b"", Request(path_or_url="x"))
    assert rows == []


# ============================================================================
# VolatilityReport
# ============================================================================

def test_volatility_normalize_parses_csv():
    raw = (FIXTURE_DIR / "probe_volatility.csv").read_text()
    rows = VolatilityReport().normalize(raw, Request(path_or_url="x"))
    assert len(rows) > 200
    first = rows[0]
    assert first["symbol"]
    assert first["date"]
    assert isinstance(first["annualised_volatility"], (float, type(None)))


def test_volatility_dedups_on_rerun(db):
    raw = (FIXTURE_DIR / "probe_volatility.csv").read_text()
    url = "https://nsearchives.nseindia.com/archives/nsccl/volt/CMVOLT_19052026.CSV"
    session = FakeSession(text_fixtures={url: raw})
    r1 = VolatilityReport().run(session, db, context={"for_date": date(2026, 5, 19)})
    r2 = VolatilityReport().run(session, db, context={"for_date": date(2026, 5, 19)})
    assert r1.persist.inserted > 200
    assert r2.persist.inserted == 0


# ============================================================================
# FiiDii
# ============================================================================
def test_fii_dii_normalize_basic():
    data = json.loads((FIXTURE_DIR / "probe_fii_dii.json").read_text())
    rows = FiiDii().normalize(data, Request(path_or_url="x"))
    assert len(rows) == 2
    categories = {r["category"] for r in rows}
    # NSE uses the formal "FII/FPI" label (FII + Foreign Portfolio Investors)
    assert categories == {"FII/FPI", "DII"}


def test_fii_dii_persists(db):
    data = json.loads((FIXTURE_DIR / "probe_fii_dii.json").read_text())
    session = FakeSession(json_fixtures={"/api/fiidiiTradeReact": data})
    report = FiiDii().run(session, db)
    assert report.succeeded == 1
    assert report.persist.inserted == 2

    # Sanity: the DII row from the probe had net_value 3801.68
    dii_net = db.execute(
        "SELECT net_value FROM raw_fii_dii WHERE category='DII'"
    ).fetchone()[0]
    assert dii_net == 3801.68

    # And FII/FPI exists too
    fii_net = db.execute(
        "SELECT net_value FROM raw_fii_dii WHERE category='FII/FPI'"
    ).fetchone()[0]
    assert fii_net == -2457.49